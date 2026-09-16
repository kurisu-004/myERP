"""批次共享原语（2026-07-29 批次化）：拆分 + 工单 rollup。

`PartService` 与 `DeliveryNoteService` 共用（送货单 add_parts 部分量自动拆、
pickup 批次级 deliver 后的工单 rollup），避免两处各写一份漂移。

事务边界：函数只在调用方 session 上 flush（repo.create/update/events.create），
commit 由调用方所在请求/任务统一负责。

2026-09-16 PR-3：
- `split_batch` 不再继承源批次的 ``next_process_id`` / ``placed_at``（已删列），
  改为继承 ``current_process_step_id``（→ t_process_chain_step.id）。
- `rollup_part_status` PR-3 第 1/3 轮修复：写入 part.next_process_id（物化列）
  前先按 least.current_process_step_id 取对应 chain step 的 process_id，再写
  process_id（而非 step.id）。v1 业务端点已 dormant，本字段仅打印 / MCP
  旁路消费；写 process_id 可让 dormant 读端 `process_map[process_id]` 仍
  取到正确工序名（写 step.id 会落空）。Rust v2 端会从同源派生。
"""
from __future__ import annotations

from datetime import date

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model import TPart, TPartBatch, TPartEvent, TProcessChainStep
from model.enums import PartEventType, PartStatus
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from utils.id_gen import new_id

# 工单 rollup 进度序：工单派生状态 = 「最落后」的活跃批次状态。
ROLLUP_PROGRESS: dict[str, int] = {
    "PENDING": 0,
    "PROGRAMMING": 1,
    "IN_PROCESS": 2,
    "REPAIRING": 2,
    "OUTSOURCE": 3,
    "INSPECTION": 4,
    "READY_TO_SHIP": 5,
    "DELIVERED": 6,
}
TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "CANCELLED"})


async def split_batch(
    *,
    batches: PartBatchRepository,
    events: PartEventRepository,
    part: TPart,
    batch: TPartBatch,
    qty: int,
    user_id: int | None,
) -> TPartBatch:
    """从 `batch` 拆出 `qty` 件为新批次（继承源批次状态/位置/holder/工艺链 step）。

    - 并发：`get_for_update`（FOR UPDATE + populate_existing）锁源批次行；
      锁内重校验数量边界。batch_no 锁内 MAX+1 保证 (part_id, batch_no) 唯一。
    - 新批次 **不继承** delivery_note_id（跟单量留在源批次；要跟单重新挂）。
    - 写 SPLIT 事件（挂在新批次上，quantity=拆出量）。

    2026-09-16 PR-3 字段继承口径：保留 status / location / current_holder_id /
    current_process_step_id（替代原 next_process_id）。`placed_at` 列已删，
    不再继承；part 层面无 `placed_at` 概念。
    """
    locked = await batches.get_for_update(batch.id)
    if locked is None:
        raise BizError(
            code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
            message=f"batch {batch.id} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    batch = locked
    if qty <= 0 or qty >= batch.quantity:
        raise BizError(
            code=ErrCode.BIZ_PART_BATCH_INVALID_QUANTITY,
            message=(
                f"拆分数量必须 ∈ [1, {batch.quantity - 1}]"
                f"（批次 {batch.batch_no} 当前 {batch.quantity} 件），got {qty}"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    batch_no = await batches.next_batch_no(part.id)
    new_batch = TPartBatch(
        id=new_id(),
        part_id=part.id,
        batch_no=batch_no,
        quantity=qty,
        status=batch.status,
        location=batch.location,
        current_holder_id=batch.current_holder_id,
        # 2026-09-16 PR-3：继承源批次的 current_process_step_id（→ t_process_chain_step.id）。
        # 原 next_process_id / placed_at 列已删，不再继承。
        current_process_step_id=batch.current_process_step_id,
        delivery_note_id=None,
        parent_batch_id=batch.id,
    )
    new_batch._part_serial = part.serial_no  # transient：SM 事件 drawing_code 用
    new_batch.created_by = user_id
    new_batch.updated_by = user_id
    batch.quantity -= qty
    batch.updated_by = user_id
    await batches.create(new_batch)
    await batches.update(batch)
    await events.create(TPartEvent(
        id=new_id(),
        part_id=part.id,
        batch_id=new_batch.id,
        event_type=PartEventType.SPLIT.value,
        from_status=batch.status,
        to_status=batch.status,
        quantity=qty,
        note=f"从批次 {batch.batch_no} 拆分 {qty} 件 → 批次 {batch_no}",
        created_by=user_id,
    ))
    return new_batch


async def rollup_part_status(
    *,
    batches: PartBatchRepository,
    events: PartEventRepository,
    part: TPart,
    user_id: int | None,
) -> None:
    """批次流转后重算工单派生状态（就地改 part 字段；caller 负责 update/flush）。

    规则：
    - 有活跃批次 → part.status/location/holder =
      「最落后」活跃批次的同名字段（进度序见 ROLLUP_PROGRESS，同级取 batch_no 小者）。
    - 全部终态 → 全部 CANCELLED ⇒ CANCELLED，否则 COMPLETED；
      释放 serial_no（回池），写工单级终态事件（batch_id=NULL）。
    - 全部活跃批次都已 DELIVERED 且 actual_delivery_date 未填 → 记今天。

    2026-09-16 PR-3 第 1/3 轮修复：part.next_process_id（rollup 物化列）写
    入前先按 least.current_process_step_id 取对应 chain step 的 process_id，
    再写 process_id（而非 step.id）；最落后批次无 step / step 已软删 → None。
    之前直接复制 step.id 的写法会让下游打印 / MCP 旁路按 step.id 查
    t_process 落空。v1 业务端点已 dormant，仅本字段的 dormant 读端（print /
    MCP）消费此值，写 process_id 可让 `process_map[process_id]` 仍取到正确
    工序名。Rust v2 端从同源派生，语义对齐。
    """
    all_batches = await batches.list_by_part(part.id)
    if not all_batches:
        return  # 防御：正常工单至少有根批次
    prev_status = part.status
    active = [b for b in all_batches if b.status not in TERMINAL_STATUSES]
    if not active:
        all_cancelled = all(b.status == "CANCELLED" for b in all_batches)
        part.status = "CANCELLED" if all_cancelled else "COMPLETED"
        part.location = None
        part.current_holder_id = None
        part.next_process_id = None
        part.serial_no = None  # 释放回 serial 池
        if prev_status != part.status:
            await events.create(TPartEvent(
                id=new_id(),
                part_id=part.id,
                batch_id=None,
                event_type=(
                    PartEventType.CANCELLED.value
                    if part.status == "CANCELLED"
                    else PartEventType.COMPLETED.value
                ),
                from_status=prev_status,
                to_status=part.status,
                quantity=None,
                note="全部批次终态，工单自动 rollup",
                created_by=user_id,
            ))
        return
    least = min(
        active,
        key=lambda b: (ROLLUP_PROGRESS.get(b.status, 0), b.batch_no),
    )
    part.status = least.status
    part.location = least.location
    part.current_holder_id = least.current_holder_id
    # 2026-09-16 PR-3 修复：t_part_batch.next_process_id 列已删；不能直接写
    # least.current_process_step_id（step.id）到 part.next_process_id
    # （物化列期望 process.id，否则下游打印 / MCP 旁路会按 step.id 去查
    # t_process 而落空）。先按 least.current_process_step_id 取对应 chain
    # step 的 process_id 再写入；最落后批次无 step / step 已软删 → None。
    # v1 业务端点已 dormant，本字段仅打印 / MCP 旁路消费，dormant 读端用
    # process_map[process_id] 仍能取到正确工序名（详见模块 docstring）。
    from sqlalchemy import select
    least_step_pid: int | None = None
    if least.current_process_step_id is not None:
        pid_row = await batches.session.execute(
            select(TProcessChainStep.process_id).where(
                TProcessChainStep.id == least.current_process_step_id,
                TProcessChainStep.deleted_at.is_(None),
            )
        )
        least_step_pid = pid_row.scalar_one_or_none()
    part.next_process_id = least_step_pid
    # 全部活跃批次都已送出（DELIVERED）→ 记实际交付日（仅首次）
    if (
        part.actual_delivery_date is None
        and all(b.status == "DELIVERED" for b in active)
    ):
        part.actual_delivery_date = date.today()
