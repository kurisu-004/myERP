"""批次共享原语（2026-07-29 批次化）：拆分 + 工单 rollup。

`PartService` 与 `DeliveryNoteService` 共用（送货单 add_parts 部分量自动拆、
pickup 批次级 deliver 后的工单 rollup），避免两处各写一份漂移。

事务边界：函数只在调用方 session 上 flush（repo.create/update/events.create），
commit 由调用方所在请求/任务统一负责。

2026-09-16 PR-3：
- `split_batch` 不再继承源批次的 ``next_process_id`` / ``placed_at``（已删列），
  改为继承 ``current_process_step_id``（→ t_process_chain_step.id）。
- `rollup_part_status` 仍把「最落后」活跃批次的 ``current_process_step_id``
  写回 part 的 ``next_process_id`` 物化列（v1 端用作 rollup 缓存；Rust v2 端
  也会从同源派生）。Rust v2 端最终会替换为从 step.process_id 派生的语义，
  v1 端的语义短期保留以不破坏现有 v1 API 读路径。
"""
from __future__ import annotations

from datetime import date

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model import TPart, TPartBatch, TPartEvent
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

    2026-09-16 PR-3：part.next_process_id 仍是 rollup 物化列；v1 端暂保留
    当前持久口径（直接复制最落后批次的 current_process_step_id 当作
    next_process_id 值；语义上有损——step.id 不是 process.id——v1 业务
    端点已 dormant，不暴露给前端；Rust v2 端会改写为从 step.process_id
    派生）。v1 仅打印端点 / MCP 路径不依赖该字段的语义正确性。
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
    # 2026-09-16 PR-3：t_part_batch.next_process_id 列已删；这里直接写
    # current_process_step_id 到 part.next_process_id 物化列作为缓存，
    # v1 dormant 业务端点不再消费，仅打印 / MCP 旁路使用，语义有损但
    # 不影响正确性（详见 docstring）。
    part.next_process_id = least.current_process_step_id
    # 全部活跃批次都已送出（DELIVERED）→ 记实际交付日（仅首次）
    if (
        part.actual_delivery_date is None
        and all(b.status == "DELIVERED" for b in active)
    ):
        part.actual_delivery_date = date.today()
