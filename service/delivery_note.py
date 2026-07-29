"""送货单管理 service（2026-07-22 新增；替代老 stateless XLSX 导出）。

完整状态机：DRAFT ↔ SUBMITTED → PICKED_UP → ARCHIVED。
文员草拟 / 添加零件 / 提交 / 撤回 / 软删（仅 DRAFT）；
司机扫齐图纸码后一键领取，原子完成「全员 part.deliver() + 单据归档」。

依赖：
- DeliveryNoteRepository（CRUD/列表/查询）
- DeliveryNoteEventRepository（事件流 append-only）
- DeliveryNoteCounterRepository（每日单号 DN-YYYYMMDD-NNNN）
- PartRepository / PartEventRepository（pickup 时联动 part.deliver）
- CustomerRepository（建单校验客户存在）
- WorkerRepository（pickup 校验司机工种 / 活跃）
- current_user（service 层已注入 `_user_id`）

注意：
- 不在状态机内部查 DB；service 层先调 `refresh_for_state_machine` 再 send。
- add/remove 零件时不走状态机；显式写 ITEM_ADDED / ITEM_REMOVED 事件。
- 撤回（SUBMITTED → DRAFT）按 plan §4 决策点 a 的方案 B 落地：
  保留关联 `t_part.delivery_note_id`，只回退 note.status；
  转单仍要走本单的 `remove_parts`（决策 a 的取舍）。
- 单号在新草稿 `create_draft` 时直接发放；行 submitted 后即不可变号。
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from model.delivery_note import TDeliveryNote
from model.enums import (
    DeliveryNoteSortKey,
    DeliveryNoteStatus,
    PartStatus,
)
from model.part import TPart
from model.part_batch import TPartBatch
from repository.customer import CustomerRepository
from repository.delivery_note import (
    DeliveryNoteCounterRepository,
    DeliveryNoteEventRepository,
    DeliveryNoteRepository,
)
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.worker import WorkerRepository
from repository.work_type import WorkTypeRepository
from service._delivery_note_events import write_created
from service._id_parse import parse_snowflake_id
from service._session_refresh import refresh_for_state_machine
from utils.id_gen import new_id

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from schema.delivery_note import (
        DeliveryNoteDetailOut,
        DeliveryNoteEventOut,
        DeliveryNoteOut,
        DeliveryNotePickupScanOut,
    )


# ============================================================
# 常量
# ============================================================
WORK_TYPE_DRIVER_CODE = "送货司机"  # 与 worker.work_type.code 严格一致


class DeliveryNoteService:
    """送货单管理 service（详见模块 docstring）。"""

    def __init__(
        self,
        session: "AsyncSession",
        notes: DeliveryNoteRepository,
        note_events: DeliveryNoteEventRepository,
        counter: DeliveryNoteCounterRepository,
        parts: PartRepository,
        customers: CustomerRepository,
        workers: WorkerRepository,
        part_events: PartEventRepository,
        current_user,
        work_types: WorkTypeRepository | None = None,
        part_batches: PartBatchRepository | None = None,
        broadcaster=None,
        event_broadcaster=None,
    ) -> None:
        self.session = session
        self.notes = notes
        self.note_events = note_events
        self.counter = counter
        self.parts = parts
        self.part_batches = part_batches
        self.customers = customers
        self.workers = workers
        self.work_types = work_types
        self.part_events = part_events
        self._user_id = (
            current_user.id if current_user and hasattr(current_user, "id")
            else None
        )
        self._broadcaster = broadcaster
        self._event_broadcaster = event_broadcaster

    def _batches(self) -> PartBatchRepository:
        if self.part_batches is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing part batch repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return self.part_batches

    async def _note_batches(self, note_id: int) -> list[tuple[TPartBatch, TPart]]:
        """本单的全部行（批次, 工单）。"""
        return await self._batches().list_by_delivery_note(note_id)

    # ============================================================
    # 一览 (clerk/manager)
    # ============================================================

    async def list_with_filters(
        self,
        *,
        statuses: list[str] | None = None,
        customer_id: str | None = None,
        keyword: str | None = None,
        sort_by: DeliveryNoteSortKey = DeliveryNoteSortKey.CREATED_AT,
        sort_dir=None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        from model.enums import SortDir
        sort_dir = sort_dir or SortDir.DESC
        cid_int = (
            parse_snowflake_id(customer_id, field_name="customer_id")
            if customer_id else None
        )
        rows = await self.notes.list_with_filters(
            statuses=statuses,
            customer_id=cid_int,
            keyword=keyword,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
        total = await self.notes.count_with_filters(
            statuses=statuses, customer_id=cid_int, keyword=keyword,
        )
        from schema.delivery_note import DeliveryNoteOut
        return (
            [await self._to_out(n) for n in rows],
            total,
        )

    async def list_for_pickup(
        self, customer_id: str | None = None,
    ) -> list:
        """司机扫码台用：列出 SUBMITTED 的非软删单。"""
        cid_int = (
            parse_snowflake_id(customer_id, field_name="customer_id")
            if customer_id else None
        )
        rows = await self.notes.list_for_pickup(customer_id=cid_int)
        from schema.delivery_note import DeliveryNoteOut
        return [await self._to_out(n) for n in rows]

    # ============================================================
    # CRUD
    # ============================================================

    async def create_draft(
        self,
        customer_id: str,
        note: str | None = None,
        delivery_date: date | None = None,
        initial_items: "list | None" = None,
    ):
        cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
        cust = await self.customers.get_by_id(cid_int)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 2026-07-23 修复：送货单必须挂在一级客户（L1 root，parent_id IS NULL）
        # 下面；二级叶子节点的客户会被拒，便于 add_parts 按 L1 root 收齐跨子厂件。
        if cust.parent_id is not None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"customer {customer_id} 不是一级客户"
                    f"（L2 子节点 {cust.name}）；"
                    "送货单必须挂在一级客户下"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        today = now_naive().strftime("%Y%m%d")
        nn = await self.counter.acquire_no(today)
        effective_delivery_date = delivery_date or now_naive().date()
        obj = TDeliveryNote(
            id=new_id(),
            delivery_note_no=f"DN-{today}-{nn:04d}",
            customer_id=cid_int,
            status=DeliveryNoteStatus.DRAFT.value,
            note=note,
            delivery_date=effective_delivery_date,
        )
        if self._user_id is not None:
            obj.created_by = self._user_id
            obj.updated_by = self._user_id
        await self.notes.create(obj)
        await write_created(
            self.session,
            note_id=obj.id,
            note=f"create draft for customer {cust.name}",
            created_by=self._user_id,
        )
        await self._flush()

        # 2026-07-23 增强：原子带入首批零件。
        # 走一次 add_parts，复用既有 INSPECTION/READY_TO_SHIP 状态 + L1 校验；
        # 失败抛 BizError 向上；同一事务整体回滚（_flush 已写过一行 CREATED 事件，
        # 但 t_delivery_note / event / counter 的写都在 session 内，调用方 BizError 后
        # 由 FastAPI 异常路径 rollback 时一并撤销）。
        if initial_items:
            await self.add_parts(
                note_id=str(obj.id),
                items=list(initial_items),
                version=obj.version,
            )

        from schema.delivery_note import DeliveryNoteOut
        return await self._to_out(obj)

    async def get_with_parts(self, note_id: str):
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 2026-07-29 批次级：行=批次
        note_batches = await self._note_batches(nid_int)
        # 2026-07-23 改：后端不再维护扫码状态；传空 set 让前端从本地 Set 驱动显示。
        scanned: set[str] = set()
        from schema.delivery_note import DeliveryNoteDetailOut
        return await self._to_detail(obj, note_batches, scanned)

    async def soft_delete(
        self, note_id: str, version: int,
    ) -> None:
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if obj.status != DeliveryNoteStatus.DRAFT.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_DRAFT,
                message="only DRAFT delivery notes can be soft-deleted",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        # 关联批次同步：delivery_note_id 清 NULL（2026-07-29 批次级）
        for b, _p in await self._note_batches(nid_int):
            b.delivery_note_id = None
            b.updated_by = self._user_id
            await self._batches().update(b)
        obj.updated_by = self._user_id
        await self.notes.soft_delete(obj)

    # ============================================================
    # 草稿期 add / remove 零件
    # ============================================================

    async def add_parts(
        self,
        note_id: str,
        items: "list",
        version: int,
    ):
        """入单（2026-07-29 批次级）：items = [{batch_id, quantity?}]。

        - 批次状态必须 ∈ {INSPECTION, READY_TO_SHIP}；
        - 批次所属工单 L1 root 必须等于 note.customer_id；
        - quantity < 批次量 → 先拆分（新批次继承状态但不继承送货单），
          再把**新批次**挂上本单；缺省 / 等于批次量 → 整批挂上；
        - 批次已挂另一张 active 单（DRAFT/SUBMITTED）→ 拒。
        """
        from service._batch_ops import split_batch as _split

        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        # 2026-07-23 Bug 5：SUBMITTED 后冻结零件清单。想改动必须先 recall → DRAFT。
        if obj.status != DeliveryNoteStatus.DRAFT.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_PARTS_LOCKED,
                message=(
                    f"送货单已提交（{obj.status}），不能新增零件；"
                    "如需调整请先撤回。"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )

        # 解析批次 + 批查工单
        parsed: list[tuple[int, int | None]] = []
        for it in items:
            bid = parse_snowflake_id(it.batch_id, field_name="batch_id")
            if bid is None:
                raise BizError(
                    code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
                    message=f"batch_id 不是合法的雪花 ID：{it.batch_id!r}",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            parsed.append((bid, it.quantity))
        if not parsed:
            return await self.get_with_parts(note_id)

        batches_by_id: dict[int, TPartBatch] = {}
        for bid, _qty in parsed:
            b = await self._batches().get_by_id(bid)
            if b is None:
                raise BizError(
                    code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
                    message=f"batch {bid} 不存在或已删除",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            batches_by_id[bid] = b
        part_ids = {b.part_id for b in batches_by_id.values()}
        part_list = await self.parts.list_by_ids(list(part_ids), include_deleted=False)
        part_map: dict[int, TPart] = {p.id: p for p in part_list}

        # 批查 part 各自 customer（找 L1 root）
        part_customer_ids = {p.customer_id for p in part_list}
        part_customer_list = await self.customers.list_by_ids(
            list(part_customer_ids | {obj.customer_id})
        )
        cust_map: dict[int, Any] = {c.id: c for c in part_customer_list}

        serials_added: list[str] = []
        for bid, qty in parsed:
            batch = batches_by_id[bid]
            part = part_map.get(batch.part_id)
            if part is None:
                raise BizError(
                    code=ErrCode.BIZ_PART_NOT_FOUND,
                    message=f"batch {bid} 所属工单 {batch.part_id} 不存在",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if batch.status not in (
                PartStatus.INSPECTION.value,
                PartStatus.READY_TO_SHIP.value,
            ):
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY,
                    message=(
                        f"part {part.id} 批次 {batch.batch_no} status={batch.status}, "
                        "only INSPECTION / READY_TO_SHIP is allowed at draft entry"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            part_cust = cust_map.get(part.customer_id)
            if part_cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=(
                        f"part {part.id} 所属客户 {part.customer_id} not found"
                    ),
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            part_l1_id = (
                part_cust.id if part_cust.parent_id is None
                else part_cust.parent_id
            )
            if part_l1_id != obj.customer_id:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PARTS_MULTIPLE_CUSTOMERS,
                    message=(
                        f"part {part.id} 一级客户 {part_l1_id} != "
                        f"note 一级客户 {obj.customer_id}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if batch.delivery_note_id is not None and batch.delivery_note_id != obj.id:
                # 仅当「另一张 active 单」时挡；归档过的单允许重新挂回
                other = await self.notes.get_by_id(batch.delivery_note_id)
                if other is not None and other.status in (
                    DeliveryNoteStatus.DRAFT.value,
                    DeliveryNoteStatus.SUBMITTED.value,
                ):
                    raise BizError(
                        code=ErrCode.BIZ_DELIVERY_NOTE_PART_ALREADY_ASSIGNED,
                        message=(
                            f"part {part.id} 批次 {batch.batch_no} already on "
                            f"active delivery note {batch.delivery_note_id}"
                        ),
                        http_status=http_status.HTTP_400_BAD_REQUEST,
                    )
            # 部分量 → 拆；拆出的新批次才是入单目标
            target = batch
            if qty is not None:
                if qty <= 0 or qty > batch.quantity:
                    raise BizError(
                        code=ErrCode.BIZ_PART_BATCH_INVALID_QUANTITY,
                        message=(
                            f"入单数量必须 ∈ [1, {batch.quantity}]"
                            f"（批次 {batch.batch_no} 当前 {batch.quantity} 件），got {qty}"
                        ),
                        http_status=http_status.HTTP_400_BAD_REQUEST,
                    )
                if qty < batch.quantity:
                    target = await _split(
                        batches=self._batches(), events=self.part_events,
                        part=part, batch=batch, qty=qty, user_id=self._user_id,
                    )
            if target.delivery_note_id != obj.id:
                target.delivery_note_id = obj.id
                target.updated_by = self._user_id
                await self._batches().update(target)
                serials_added.append(part.serial_no or part.drawing_no or str(part.id))

        # 2026-07-23 移除 write_item_added 事件写：非状态机迁移噪音事件按用户
        # 要求不再记录；add_parts 的成功/失败由 service 自身返回值 + 响应体承担。
        await self._flush()

        if self._broadcaster is not None:
            await self._broadcaster()

        return await self.get_with_parts(note_id)

    async def remove_parts(
        self,
        note_id: str,
        batch_ids: list[str],
        version: int,
    ):
        """DRAFT 允许移除（2026-07-23 Bug 5：SUBMITTED 后冻结；2026-07-29 批次级）。"""
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        # 2026-07-23 Bug 5：SUBMITTED 后冻结零件清单；与 add_parts 同步。
        if obj.status != DeliveryNoteStatus.DRAFT.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_PARTS_LOCKED,
                message=(
                    f"送货单已提交（{obj.status}），不能移除零件；"
                    "如需调整请先撤回。"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )

        ids = [
            parse_snowflake_id(s, field_name="batch_ids")
            for s in batch_ids
        ]
        if not ids:
            return await self.get_with_parts(note_id)

        for bid in ids:
            if bid is None:
                continue
            b = await self._batches().get_by_id(bid)
            if b is None or b.delivery_note_id != obj.id:
                # 部分缺失：只清空确实属于本单的
                continue
            b.delivery_note_id = None
            b.updated_by = self._user_id
            await self._batches().update(b)

        # 2026-07-23 移除 write_item_removed 事件写：非状态机迁移噪音事件按用户
        # 要求不再记录。
        await self._flush()

        if self._broadcaster is not None:
            await self._broadcaster()

        return await self.get_with_parts(note_id)

    # ============================================================
    # 状态机迁移：DRAFT ↔ SUBMITTED → PICKED_UP → ARCHIVED
    # ============================================================

    async def submit(self, note_id: str, version: int):
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if obj.status != DeliveryNoteStatus.DRAFT.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_TRANSITION,
                message=(
                    f"only DRAFT can be submitted, current={obj.status}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 提交校验：所有批次仍是 READY_TO_SHIP（不能被别人改过；2026-07-29 批次级）
        note_batches = await self._note_batches(nid_int)
        if not note_batches:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_VALUE,
                message="empty delivery note; add parts before submit",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        for b, p in note_batches:
            if b.status != PartStatus.READY_TO_SHIP.value:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY,
                    message=(
                        f"part {p.id} 批次 {b.batch_no} status={b.status} "
                        "(must be READY_TO_SHIP at submit)"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
        await refresh_for_state_machine(
            self.session, obj, attrs=("status", "version", "updated_at"),
        )
        obj.sm.submit(
            event_repo=self.note_events, created_by=self._user_id,
        )
        obj.submitted_by = self._user_id
        obj.updated_by = self._user_id
        await self.notes.update(obj)
        await self._flush()

        if self._broadcaster is not None:
            await self._broadcaster()
        # 2026-07-23 修复：post-flush refresh updated_at 防 MissingGreenlet
        await self._serialize_after_mutation(obj)
        from schema.delivery_note import DeliveryNoteOut
        return await self._to_out(obj)

    async def recall(self, note_id: str, version: int):
        """SUBMITTED → DRAFT（决策点 a 落地 B：保留 part 关联）。"""
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if obj.status != DeliveryNoteStatus.SUBMITTED.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_SUBMITTED,
                message=(
                    f"only SUBMITTED can be recalled, current={obj.status}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        await refresh_for_state_machine(
            self.session, obj, attrs=("status", "version", "updated_at"),
        )
        obj.sm.recall(
            event_repo=self.note_events, created_by=self._user_id,
        )
        obj.updated_by = self._user_id
        await self.notes.update(obj)
        await self._flush()

        await self._serialize_after_mutation(obj)
        from schema.delivery_note import DeliveryNoteOut
        return await self._to_out(obj)

    # ============================================================
    # 司机扫码 + 领取
    # ============================================================

    async def pickup_scan(
        self,
        note_id: str,
        part_serial: str,
        badge_code: str | None = None,
    ):
        """司机每扫一个 part，调一次。

        2026-07-23 改：累积进度由前端本地维护（DispatchNoteList.vue 的
        ``states[noteId].scanned`` Set），后端不再写 PICKUP_SCANNED 事件，
        也不再维护 ``_scanned_serials``。本接口仅保留：
        1) 状态机校验（必须 SUBMITTED）；
        2) ``part_serial`` 属于本单的硬校验（防越权 / 防错扫其他单据）；
        3) 返回结构仍兼容前端（前端不再用 scanned_serials 覆盖本地状态）。

        返回 ``scanned_count / scanned_serials`` 恒为 0 / []；
        ``ready`` 仅在 ``expected_count > 0`` 时才可能为 True（这里实际恒 False，
        由前端基于本地 Set 判断）。
        """
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.status != DeliveryNoteStatus.SUBMITTED.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_SUBMITTED,
                message=(
                    f"only SUBMITTED can be scanned, current={obj.status}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 校验 part_serial 属于本单（2026-07-29 批次级：该工单有批次挂在本单即合法；
        # 司机扫一次 serial 即覆盖本单上该工单的全部批次行——v1 实物只有工单标签）
        part = await self.parts.get_by_serial(part_serial)
        note_batches = await self._note_batches(nid_int)
        if part is None or not any(
            b.part_id == part.id for b, _p in note_batches
        ):
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_SCAN_MISMATCH,
                message=(
                    f"serial {part_serial!r} is not in this delivery note"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 列出本单行项目总数（批次行），让前端能据此判断 ready
        expected_count = len(note_batches)
        from schema.delivery_note import DeliveryNotePickupScanOut
        return DeliveryNotePickupScanOut(
            delivery_note_id=str(obj.id),
            scanned_count=0,
            expected_count=expected_count,
            # 后端无扫码状态数据源；ready 留给前端基于本地 Set 判定。
            ready=False,
            scanned_serials=[],
        )

    async def pickup(
        self,
        note_id: str,
        driver_worker_id: str,
        version: int,
        badge_code: str | None = None,
    ):
        """扫齐后司机正式领取：原子完成「全员 part.deliver + 单据归档」。

        - driver_worker_id: 工人工牌码（雪花 ID str）对应的 t_worker.id
        - 必须 work_type.code == '送货司机' 且 is_active
        """
        nid_int = parse_snowflake_id(note_id, field_name="id")
        driver_id_int = parse_snowflake_id(
            driver_worker_id, field_name="driver_worker_id",
        )
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if obj.status != DeliveryNoteStatus.SUBMITTED.value:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_SUBMITTED,
                message=(
                    f"only SUBMITTED can be picked up, current={obj.status}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 司机校验
        driver = await self.workers.get_by_id(driver_id_int)
        if driver is None or not driver.is_active:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_DRIVER_INVALID,
                message=(
                    f"driver worker {driver_worker_id!r} not found or inactive"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        # 2026-07-23 修复：TWorker 无 work_type relationship，只有 work_type_id；
        # 必须经 WorkTypeRepository 解析工种码（对照 PartService.deliver）。
        # 旧代码 `driver.work_type.code` 会 AttributeError，导致 pickup 全程无法完成。
        driver_wt = (
            await self.work_types.get_by_id(driver.work_type_id)
            if self.work_types and driver.work_type_id else None
        )
        if driver_wt is None or driver_wt.code != WORK_TYPE_DRIVER_CODE:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_DRIVER_INVALID,
                message=(
                    f"driver work type {driver_wt.code if driver_wt else 'NULL'} "
                    f"!= {WORK_TYPE_DRIVER_CODE!r}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 校验扫齐
        # 2026-07-23 改：后端不再维护 PICKUP_SCANNED 事件 / `_scanned_serials`，
        # 司机扫码进度由前端本地 Set 跟踪；前端在 confirmDelivery() 里校验 ready
        # 再调 pickup。后端这里只校验「非空 + 状态机 SUBMITTED」，
        # 「缺漏件」的强制保证由前端负责。
        # 2026-07-29 批次级：行=批次。
        note_batches = await self._note_batches(nid_int)
        expected_count = len(note_batches)
        if expected_count == 0:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_VALUE,
                message="empty delivery note; cannot pick up",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        from service._batch_ops import rollup_part_status as _rollup

        # —— 同事务内全员 batch.deliver + 单据 pickup / archive ——
        for b, p in note_batches:
            if b.status != PartStatus.READY_TO_SHIP.value:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY,
                    message=(
                        f"part {p.id} 批次 {b.batch_no} status={b.status}, "
                        "must be READY_TO_SHIP at pickup"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            await refresh_for_state_machine(
                self.session, b,
                attrs=("status", "location", "next_process_id",
                       "version", "updated_at"),
            )
            # PartStateMachine.on_deliver 会写一行 TPartEvent（STATUS_CHANGED
            # from READY_TO_SHIP to DELIVERED，带 batch_id + quantity）
            b._part_serial = p.serial_no  # transient：SM 事件 drawing_code 用
            b.sm.deliver(
                worker=driver,
                event_repo=self.part_events,
                created_by=self._user_id,
            )
            # 清多态 holder / location（顺手修老 bug：state machine 不动 current_holder_id）
            b.current_holder_id = None
            b.location = None
            # 2026-07-23 决策：保留 b.delivery_note_id 以便 PICKED_UP/ARCHIVED 后
            # 仍可走 /print 端点打印归档原件；add_parts 的「已在别单」校验只挡 active
            # 单（DRAFT/SUBMITTED），DELIVERED 件本身被 status 校验挡掉，不会误用。
            b.updated_by = self._user_id
            await self._batches().update(b)

        # 批次送货后按工单 rollup（actual_delivery_date 由 rollup 在全部活跃
        # 批次 DELIVERED 时写入；2026-07-29）
        for part_id in {b.part_id for b, _p in note_batches}:
            part = await self.parts.get_by_id(part_id)
            if part is None:
                continue
            await refresh_for_state_machine(
                self.session, part,
                attrs=(
                    "status", "location", "current_holder_id",
                    "next_process_id", "serial_no", "actual_delivery_date",
                ),
            )
            await _rollup(
                batches=self._batches(), events=self.part_events,
                part=part, user_id=self._user_id,
            )
            part.updated_by = self._user_id
            await self.parts.update(part)

        # 单据 SUBMITTED → PICKED_UP（2026-07-23 决策：停在 PICKED_UP，不自动
        # archive；PICKED_UP 展示为「已送货」。ARCHIVED 状态暂不使用，保留定义。）
        await refresh_for_state_machine(
            self.session, obj,
            attrs=("status", "version", "updated_at"),
        )
        obj.sm.pickup(
            event_repo=self.note_events, created_by=self._user_id,
        )
        obj.driver_worker_id = driver_id_int
        obj.picked_up_by = self._user_id
        obj.updated_by = self._user_id
        await self.notes.update(obj)
        await self._flush()

        if self._broadcaster is not None:
            await self._broadcaster()
        if self._event_broadcaster is not None:
            await self._event_broadcaster(
                "DELIVERY_NOTE_PICKED_UP",
                {
                    "delivery_note_id": obj.id,
                    "delivery_note_no": obj.delivery_note_no,
                    "part_count": expected_count,
                    "driver_worker_id": driver_id_int,
                },
            )

        # 2026-07-23 修复：pickup 的 UPDATE 之后，updated_at expired，
        # 必须 refresh 再 `_to_out` 防 MissingGreenlet
        await self._serialize_after_mutation(obj)
        from schema.delivery_note import DeliveryNoteOut
        return await self._to_out(obj)

    # ============================================================
    # 事件列表（详情时间线）
    # ============================================================

    async def list_events(self, note_id: str) -> list:
        nid_int = parse_snowflake_id(note_id, field_name="id")
        events = await self.note_events.list_by_note(nid_int)
        from schema.delivery_note import DeliveryNoteEventOut
        return [
            DeliveryNoteEventOut(
                id=str(e.id),
                delivery_note_id=str(e.delivery_note_id),
                event_type=e.event_type,
                from_status=e.from_status,
                to_status=e.to_status,
                note=e.note,
                created_by=str(e.created_by) if e.created_by else None,
                created_at=e.created_at,
            )
            for e in events
        ]

    # ============================================================
    # 2026-07-23 增强：partial update / 候选零件 / 打印
    # ============================================================

    async def update(
        self,
        note_id: str,
        *,
        version: int,
        delivery_date: "date | None" = None,
        note_text: str | None = None,
    ):
        """DRAFT / SUBMITTED 单 partial 更新：送货日期 / 备注。

        - `delivery_date=None` 视作「不改」（与 Pydantic 默认对齐）；
          未来要支持清空另加 `clear_delivery_date: bool` 字段。
        - `note_text=None` 同上「不改」；空串视作「清空」写 NULL 入 DB。
        """
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if obj.version != version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"delivery note version conflict: "
                    f"server={obj.version} client={version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if obj.status not in (
            DeliveryNoteStatus.DRAFT.value,
            DeliveryNoteStatus.SUBMITTED.value,
        ):
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_TRANSITION,
                message=(
                    f"cannot update {obj.status} note; "
                    "only DRAFT/SUBMITTED is editable"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        changed = False
        if delivery_date is not None and delivery_date != obj.delivery_date:
            obj.delivery_date = delivery_date
            changed = True
        if note_text is not None and note_text != obj.note:
            obj.note = note_text.strip() or None
            changed = True
        if changed:
            obj.updated_by = self._user_id
            await self.notes.update(obj)
            await self._flush()
            # 2026-07-23 修复：post-flush refresh 防 MissingGreenlet
            await self._serialize_after_mutation(obj)

        from schema.delivery_note import DeliveryNoteOut
        return await self._to_out(obj)

    async def list_candidate_parts(self, customer_id: str) -> list:
        """L1 根下 status ∈ {INSPECTION, READY_TO_SHIP} 的候选入单批次。

        2026-07-29 批次级：行=批次（quantity=批次量，可改小后入单自动拆）。
        - 必须传 L1 root id；L2 直接拒；
        - 不在 active 单（DRAFT/SUBMITTED）上的批次才出现；
        - 同 L1 根下所有 active 子客户 (含自身) 跨子厂一起列出。
        """
        cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
        cust = await self.customers.get_by_id(cid_int)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is not None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"customer {customer_id} 不是一级客户；"
                    "candidate-parts 必须传一级客户"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # L1 根下所有 active 子客户 (L2) + L1 自身
        children = await self.customers.list_children(cid_int, include_deleted=False)
        customer_ids = [c.id for c in children] + [cid_int]

        rows = await self._batches().list_batches_with_part(
            statuses=[PartStatus.INSPECTION.value, PartStatus.READY_TO_SHIP.value],
            customer_ids_in=customer_ids,
            limit=2000,
        )

        # 过滤：不在 active 单上（即 batch.delivery_note_id IS NULL 或仅指向
        # PICKED_UP/ARCHIVED）。
        active_note_ids: set[int] = set()
        linked_note_ids = {
            b.delivery_note_id for b, _p in rows
            if b.delivery_note_id is not None
        }
        if linked_note_ids:
            linked_notes = await self.notes.list_by_ids(list(linked_note_ids))
            active_note_ids = {
                n.id for n in linked_notes
                if n.status in (
                    DeliveryNoteStatus.DRAFT.value,
                    DeliveryNoteStatus.SUBMITTED.value,
                )
            }

        from schema.delivery_note import DeliveryNoteCandidatePart
        result: list[DeliveryNoteCandidatePart] = []
        for b, p in rows:
            if b.delivery_note_id is not None and b.delivery_note_id in active_note_ids:
                continue  # 在 active 单上，跳过
            result.append(DeliveryNoteCandidatePart(
                id=str(p.id),
                batch_id=str(b.id),
                batch_no=b.batch_no,
                batch_label=(
                    f"{p.serial_no}B{b.batch_no:02d}"
                    if p.serial_no else f"批次{b.batch_no}"
                ),
                serial_no=p.serial_no or "",
                drawing_no=p.drawing_no or "",
                name=p.name or "",
                quantity=b.quantity,
                applicant_name=p.applicant_name,
                status=b.status,
                planned_delivery_date=p.planned_delivery_date,
            ))
        return result

    async def print_xlsx(self, note_id: str) -> tuple[bytes, str]:
        """按 L1 客户前缀分发模板（template/delivery_note_{prefix}.xlsx），
        返回 (bytes, prefix)；状态不限（DRAFT/SUBMITTED/PICKED_UP/ARCHIVED 都可）。

        真正的填表逻辑在 `service/delivery_note_print.py::DeliveryNotePrintService`；
        这里只负责 note 加载 + 薄包装。
        """
        from service.delivery_note_print import DeliveryNotePrintService
        nid_int = parse_snowflake_id(note_id, field_name="id")
        obj = await self.notes.get_by_id(nid_int)
        if obj is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
                message=f"delivery note {note_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        printer = DeliveryNotePrintService(
            notes=self.notes,
            parts=self.parts,
            customers=self.customers,
            part_batches=self._batches(),
        )
        return await printer.render(obj)

    # ============================================================
    # 内部 helpers
    # ============================================================

    async def _flush(self) -> None:
        await self.session.flush()

    async def _serialize_after_mutation(self, obj: "TDeliveryNote") -> None:
        """在 mutation 路径末尾（构造响应前）确保 obj 的 server-side 列
        已 async-loaded。

        2026-07-23 修复：``AuditMixin.updated_at`` 是 ``onupdate=func.now()``
        server-side 列，每次 ORM flush 后会被 SQLAlchemy 标为 expired。后续
        ``_to_out(obj)`` 同步读 ``obj.updated_at`` 会触发隐式 SELECT，
        在 AsyncSession 下抛 ``MissingGreenlet`` —— 表现是
        ``POST /delivery-notes/{id}/submit`` 报 500 DATABASE_ERROR。

        解法：每次 ``notes.update(obj) → _flush()`` 之后、``_to_out(obj)`` 之前
        显式 ``session.refresh(obj, attribute_names=("updated_at",))``。
        只刷必要列，避免覆盖同事务内尚未 flush 的其他字段。

        所有 mutation 路径（submit / recall / pickup / update）统一调用本方法
        避免漂移。
        """
        await refresh_for_state_machine(
            self.session, obj, attrs=("updated_at",),
        )

    # ============================================================
    # 内部：ORM → 出参
    # ============================================================

    async def _to_out(self, obj: TDeliveryNote):
        from schema.delivery_note import DeliveryNoteOut
        # 2026-07-29 批次级：行数=批次数
        part_count = len(await self._note_batches(obj.id))
        cust = await self.customers.get_by_id(obj.customer_id)
        customer_name = cust.name if cust else None
        parent_name = None
        path = None
        if cust:
            if cust.parent_id:
                parent = await self.customers.get_by_id(cust.parent_id)
                parent_name = parent.name if parent else None
                path = (
                    f"{parent_name} / {customer_name}"
                    if parent_name else customer_name
                )
            else:
                parent_name = customer_name
                path = customer_name
        driver_name: str | None = None
        if obj.driver_worker_id:
            d = await self.workers.get_by_id(obj.driver_worker_id)
            if d:
                driver_name = d.name
        return DeliveryNoteOut(
            id=str(obj.id),
            version=obj.version,
            delivery_note_no=obj.delivery_note_no,
            customer_id=str(obj.customer_id),
            customer_name=customer_name,
            parent_customer_name=parent_name,
            customer_path=path,
            status=obj.status,
            submitted_at=obj.submitted_at,
            picked_up_at=obj.picked_up_at,
            submitted_by=str(obj.submitted_by) if obj.submitted_by else None,
            picked_up_by=str(obj.picked_up_by) if obj.picked_up_by else None,
            driver_worker_id=str(obj.driver_worker_id) if obj.driver_worker_id else None,
            driver_worker_name=driver_name,
            part_count=part_count,
            note=obj.note,
            delivery_date=obj.delivery_date,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )

    async def _to_detail(
        self,
        obj: TDeliveryNote,
        note_batches: list[tuple[TPartBatch, TPart]],
        scanned_serials: set[str],
    ):
        from schema.delivery_note import (
            DeliveryNoteDetailOut,
            DeliveryNoteLineItem,
        )
        head = await self._to_out(obj)

        # 2026-07-23 R2-C：一次性批查所有 part 的 L2 叶子客户 + L1 父客户，
        # 避免 N+1（沿用 delivery_note_print.py::render 同款模式）
        parts = [p for _b, p in note_batches]
        if parts:
            leaf_ids = list({p.customer_id for p in parts})
            leaf_list = await self.customers.list_by_ids(leaf_ids)
            leaf_map: dict[int, "Any"] = {c.id: c for c in leaf_list}
            parent_ids = [c.parent_id for c in leaf_list if c.parent_id]
            parent_list = (
                await self.customers.list_by_ids(list(set(parent_ids)))
                if parent_ids else []
            )
            parent_map: dict[int, "Any"] = {c.id: c for c in parent_list}
        else:
            leaf_map = {}
            parent_map = {}

        items: list[DeliveryNoteLineItem] = []
        for b, p in note_batches:
            line_serial = p.serial_no or str(p.id)
            leaf = leaf_map.get(p.customer_id)
            parent = (
                parent_map.get(leaf.parent_id)
                if leaf and leaf.parent_id else None
            )
            leaf_name = leaf.name if leaf else None
            parent_name = (
                parent.name if parent
                else (leaf_name if leaf else None)  # L1 root 自指同 leaf
            )
            path = (
                f"{parent_name} / {leaf_name}"
                if (parent_name and leaf_name and parent_name != leaf_name)
                else leaf_name
            )
            items.append(DeliveryNoteLineItem(
                id=str(b.id),
                part_id=str(p.id),
                batch_no=b.batch_no,
                batch_label=(
                    f"{p.serial_no}B{b.batch_no:02d}"
                    if p.serial_no else f"批次{b.batch_no}"
                ),
                serial_no=p.serial_no or "",
                drawing_no=p.drawing_no,
                name=p.name,
                quantity=b.quantity,
                is_urgent=p.is_urgent,
                status=b.status,
                applicant_name=p.applicant_name,
                request_date=p.request_date,
                planned_delivery_date=p.planned_delivery_date,
                system_delivery_date=p.system_delivery_date,
                order_no=p.order_no,
                note=p.note,
                customer_name=leaf_name,
                parent_customer_name=parent_name,
                customer_path=path,
                is_scanned=line_serial in scanned_serials,
                scanned=line_serial in scanned_serials,
            ))
        return DeliveryNoteDetailOut(
            **head.model_dump(),
            line_items=items,
            scanned_serials=sorted(scanned_serials),
        )
