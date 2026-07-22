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
    DeliveryNoteEventType,
    DeliveryNoteSortKey,
    DeliveryNoteStatus,
    PartStatus,
)
from model.part import TPart
from repository.customer import CustomerRepository
from repository.delivery_note import (
    DeliveryNoteCounterRepository,
    DeliveryNoteEventRepository,
    DeliveryNoteRepository,
)
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.worker import WorkerRepository
from service._delivery_note_events import (
    write_created,
    write_item_added,
    write_item_removed,
    write_pickup_scan,
)
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
        broadcaster=None,
        event_broadcaster=None,
    ) -> None:
        self.session = session
        self.notes = notes
        self.note_events = note_events
        self.counter = counter
        self.parts = parts
        self.customers = customers
        self.workers = workers
        self.part_events = part_events
        self._user_id = (
            current_user.id if current_user and hasattr(current_user, "id")
            else None
        )
        self._broadcaster = broadcaster
        self._event_broadcaster = event_broadcaster

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
        obj = TDeliveryNote(
            id=new_id(),
            delivery_note_no=f"DN-{today}-{nn:04d}",
            customer_id=cid_int,
            status=DeliveryNoteStatus.DRAFT.value,
            note=note,
        )
        if self._user_id is not None:
            obj.created_by = self._user_id
            obj.updated_by = self._user_id
        await self.notes.create(obj)
        await write_created(
            self.session,
            note_id=obj.id,
            note=f"create draft for customer {cid_int}",
            created_by=self._user_id,
        )
        await self._flush()
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
        parts = await self.notes.list_parts(nid_int)
        scanned = await self._scanned_serials(nid_int)
        from schema.delivery_note import DeliveryNoteDetailOut
        return await self._to_detail(obj, parts, scanned)

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
        # 关联 part 同步：delivery_note_id 清 NULL（让 note 唯一性规则自然让位）
        parts = await self.notes.list_parts(nid_int)
        for p in parts:
            p.delivery_note_id = None
            p.updated_by = self._user_id
            await self.parts.update(p)
        obj.updated_by = self._user_id
        await self.notes.soft_delete(obj)

    # ============================================================
    # 草稿期 add / remove 零件
    # ============================================================

    async def add_parts(
        self,
        note_id: str,
        part_ids: list[str],
        version: int,
    ):
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
        # 仅 DRAFT / SUBMITTED 允许 add（DRAFT 是常返；SUBMITTED 是允许的——
        # 用户原话"可任意添加/移除"，故 SUBMITTED 状态的「撤回前临时修正」也开放）
        if obj.status not in (
            DeliveryNoteStatus.DRAFT.value,
            DeliveryNoteStatus.SUBMITTED.value,
        ):
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_TRANSITION,
                message=(
                    f"cannot add parts to {obj.status} note; "
                    "only DRAFT/SUBMITTED is editable"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        ids = [
            parse_snowflake_id(s, field_name="part_ids")
            for s in part_ids
        ]
        # 批查 parts
        if not ids:
            return await self.get_with_parts(note_id)
        existing_list = await self.parts.list_by_ids(
            ids, include_deleted=False,
        )
        found_ids = {p.id for p in existing_list}
        missing = [pid for pid in ids if pid not in found_ids]
        if missing:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part 不存在或已删除：{missing}",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 2026-07-23 修复：批查 part 各自 customer（找 L1 root）。
        # note.customer_id 必为 L1 root（已在 create_draft 校验），所以 part L1 root
        # 等于 note.customer_id 即合法；其它都抛 BIZ_DELIVERY_NOTE_PARTS_MULTIPLE_CUSTOMERS。
        part_customer_ids = {p.customer_id for p in existing_list}
        part_customer_list = await self.customers.list_by_ids(
            list(part_customer_ids | {obj.customer_id})
        )
        cust_map: dict[int, Any] = {c.id: c for c in part_customer_list}

        # 校验：每个 part 状态 READY_TO_SHIP；part L1 root 必须等于 note.customer_id；
        # 若 part.delivery_note_id 已不为 NULL → 该 part 已在别的单上
        serials_added: list[str] = []
        for p in existing_list:
            if p.status != PartStatus.READY_TO_SHIP.value:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY,
                    message=(
                        f"part {p.id} status={p.status}, "
                        "only READY_TO_SHIP is allowed"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            # 2026-07-23 修复：L1 root 比较（note.customer_id 是 L1 root）。
            # - part.customer_id == obj.customer_id：等价是同一 L1 root
            # - part 自己的 customer 的 parent_id == obj.customer_id：part 是
            #   note L1 下的 L2 子节点（合法）
            # - 其它：跨 L1 客户的件被拒
            part_cust = cust_map.get(p.customer_id)
            if part_cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=(
                        f"part {p.id} 所属客户 {p.customer_id} not found"
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
                        f"part {p.id} 一级客户 {part_l1_id} != "
                        f"note 一级客户 {obj.customer_id}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if p.delivery_note_id is not None and p.delivery_note_id != obj.id:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_ALREADY_ASSIGNED,
                    message=(
                        f"part {p.id} already on delivery note "
                        f"{p.delivery_note_id}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if p.delivery_note_id != obj.id:
                p.delivery_note_id = obj.id
                p.updated_by = self._user_id
                await self.parts.update(p)
                serials_added.append(p.serial_no or str(p.id))

        await write_item_added(
            self.session, note_id=obj.id,
            added_serial_nos=serials_added, created_by=self._user_id,
        )
        await self._flush()

        if self._broadcaster is not None:
            await self._broadcaster()

        return await self.get_with_parts(note_id)

    async def remove_parts(
        self,
        note_id: str,
        part_ids: list[str],
        version: int,
    ):
        """DRAFT / SUBMITTED 都允许移除。"""
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
                    f"cannot remove parts from {obj.status} note; "
                    "only DRAFT/SUBMITTED is editable"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        ids = [
            parse_snowflake_id(s, field_name="part_ids")
            for s in part_ids
        ]
        if not ids:
            return await self.get_with_parts(note_id)
        existing_list = await self.parts.list_by_ids(
            ids, include_deleted=False,
        )

        serials_removed: list[str] = []
        for p in existing_list:
            if p.delivery_note_id != obj.id:
                # 部分缺失：只清空确实属于本单的
                continue
            p.delivery_note_id = None
            p.updated_by = self._user_id
            await self.parts.update(p)
            serials_removed.append(p.serial_no or str(p.id))

        await write_item_removed(
            self.session, note_id=obj.id,
            removed_serial_nos=serials_removed, created_by=self._user_id,
        )
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

        # 提交校验：所有 part 仍是 READY_TO_SHIP（不能被别人改过）
        parts = await self.notes.list_parts(nid_int)
        if not parts:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_VALUE,
                message="empty delivery note; add parts before submit",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        for p in parts:
            if p.status != PartStatus.READY_TO_SHIP.value:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY,
                    message=(
                        f"part {p.id} status={p.status} "
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

        累积进度 = 已经 PICKUP_SCANNED 事件的 drawing_code 去重；
        返回：{ scanned_count, expected_count, ready, scanned_serials }。
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

        # 校验 part_serial 属于本单
        part = await self.parts.get_by_serial(part_serial)
        if part is None or part.delivery_note_id != nid_int:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_SCAN_MISMATCH,
                message=(
                    f"serial {part_serial!r} is not in this delivery note"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 累加去重；写入 PICKUP_SCANNED 事件（含 drawing_code / badge_code）
        await write_pickup_scan(
            self.session,
            note_id=obj.id,
            drawing_code=part.serial_no or str(part.id),
            badge_code=badge_code,
            scanned_count=0,           # 真实值在下面计算后 update
            expected_count=0,
            note=None,
            created_by=self._user_id,
        )
        await self._flush()

        scanned_set = await self._scanned_serials(nid_int)
        parts = await self.notes.list_parts(nid_int)
        expected_count = len(parts)
        scanned_count = len(scanned_set)
        from schema.delivery_note import DeliveryNotePickupScanOut
        return DeliveryNotePickupScanOut(
            delivery_note_id=str(obj.id),
            scanned_count=scanned_count,
            expected_count=expected_count,
            ready=scanned_count >= expected_count and expected_count > 0,
            scanned_serials=sorted(scanned_set),
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
        if not driver.work_type or driver.work_type.code != WORK_TYPE_DRIVER_CODE:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_DRIVER_INVALID,
                message=(
                    f"driver work type {driver.work_type.code if driver.work_type else 'NULL'} "
                    f"!= {WORK_TYPE_DRIVER_CODE!r}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 校验扫齐
        parts = await self.notes.list_parts(nid_int)
        expected_count = len(parts)
        if expected_count == 0:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_INVALID_VALUE,
                message="empty delivery note; cannot pick up",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        scanned = await self._scanned_serials(nid_int)
        missing = [
            p.serial_no for p in parts
            if (p.serial_no or str(p.id)) not in scanned
        ]
        if missing:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_NOTE_SCAN_INCOMPLETE,
                message=(
                    f"扫描未齐：缺 {len(missing)} 件，"
                    f"如 {missing[:5]}{'…' if len(missing) > 5 else ''}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        today = date.today()
        # —— 同事务内全员 part.deliver + 单据 pickup / archive ——
        for p in parts:
            if p.status != PartStatus.READY_TO_SHIP.value:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY,
                    message=(
                        f"part {p.id} status={p.status}, "
                        "must be READY_TO_SHIP at pickup"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            await refresh_for_state_machine(
                self.session, p,
                attrs=("status", "location", "next_process_id",
                       "version", "updated_at"),
            )
            # PartStateMachine.on_deliver 会写一行 TPartEvent（STATUS_CHANGED
            # from READY_TO_SHIP to DELIVERED）
            p.sm.deliver(
                worker=driver,
                event_repo=self.part_events,
                created_by=self._user_id,
            )
            p.actual_delivery_date = today
            # 清多态 holder / location（顺手修老 bug：state machine 不动 current_holder_id）
            p.current_holder_id = None
            p.location = None
            # PICKED_UP 时把关联置 NULL，让前端 PartDetail「所属送货单」消失
            p.delivery_note_id = None
            p.updated_by = self._user_id
            await self.parts.update(p)

        # 单据 SUBMITTED → PICKED_UP → ARCHIVED（同一事务）
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

        obj.sm.archive(
            event_repo=self.note_events, created_by=self._user_id,
        )
        obj.updated_by = self._user_id
        await self.notes.update(obj)
        await self._flush()

        if self._broadcaster is not None:
            await self._broadcaster()
        if self._event_broadcaster is not None:
            await self._event_broadcaster(
                "DELIVERY_NOTE_ARCHIVED",
                {
                    "delivery_note_id": obj.id,
                    "delivery_note_no": obj.delivery_note_no,
                    "part_count": expected_count,
                    "driver_worker_id": driver_id_int,
                },
            )

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
                drawing_code=e.drawing_code,
                badge_code=e.badge_code,
                note=e.note,
                scanned_count=e.scanned_count,
                expected_count=e.expected_count,
                created_by=str(e.created_by) if e.created_by else None,
                created_at=e.created_at,
            )
            for e in events
        ]

    # ============================================================
    # 内部：scanned serial 累积（按 PICKUP_SCANNED 事件去重）
    # ============================================================

    async def _scanned_serials(self, note_id: int) -> set[str]:
        events = await self.note_events.list_by_note(note_id)
        return {
            e.drawing_code
            for e in events
            if e.event_type == DeliveryNoteEventType.PICKUP_SCANNED.value
            and e.drawing_code
        }

    async def _flush(self) -> None:
        await self.session.flush()

    # ============================================================
    # 内部：ORM → 出参
    # ============================================================

    async def _to_out(self, obj: TDeliveryNote):
        from schema.delivery_note import DeliveryNoteOut
        part_count = await self.notes.count_parts(obj.id)
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
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )

    async def _to_detail(
        self,
        obj: TDeliveryNote,
        parts: list[TPart],
        scanned_serials: set[str],
    ):
        from schema.delivery_note import (
            DeliveryNoteDetailOut,
            DeliveryNoteLineItem,
        )
        head = await self._to_out(obj)
        items: list[DeliveryNoteLineItem] = []
        for p in parts:
            line_serial = p.serial_no or str(p.id)
            items.append(DeliveryNoteLineItem(
                id=str(p.id),
                serial_no=p.serial_no or "",
                drawing_no=p.drawing_no,
                name=p.name,
                quantity=p.quantity,
                is_urgent=p.is_urgent,
                status=p.status,
                is_scanned=line_serial in scanned_serials,
                scanned=line_serial in scanned_serials,
            ))
        return DeliveryNoteDetailOut(
            **head.model_dump(),
            line_items=items,
            scanned_serials=sorted(scanned_serials),
        )
