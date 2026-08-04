"""PartService：零件业务逻辑层。

新流程（去掉 READY 后）：
- PENDING → IN_PROCESS (放在生产货架上；holder=shelf_id)
- IN_PROCESS (shelf↔worker 之间切换 holder；状态不变)
- IN_PROCESS → INSPECTION (送检；holder=inspection_shelf_id)
- INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED
- 任意非终态 → REPAIRING → IN_PROCESS
- 任意非终态 → CANCELLED

货架与 SHELF_ACCOUNT 的耦合已经在 api/v1 层用 `require_shelf_account_from_body`
校验；service 层只关心「传进来的 shelf_id 是否真的存在且生效」。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from core.serial import resolve_root_prefix
from model import TAssembly, TCustomer, TPart, TPartBatch, TPartEvent, TProcess, TShelf, TWorker, TWorkType
from model.enums import (
    OutsourceQuoteStatus, PartEventType, PartLocation, PartStatus, PartSortKey, ProcessCategory, ShelfZone, SortDir,
)
from repository.applicant import ApplicantRepository
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.delivery_note import DeliveryNoteRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_company_process import OutsourceCompanyProcessRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.outsource_shipment import OutsourceShipmentRepository
from repository.part_file import PartFileRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from schema.part import (
    DirectOutsourceCandidateItem,
    DirectOutsourceCandidateListOut,
    DirectOutsourceCompanyOption,
    FailInspectionRequest,
    OutsourceSendableItem,
    OutsourceSendableListOut,
    PartBatchCreateItemFailure,
    PartBatchCreateRequest,
    PartBatchCreateResult,
    PartBatchTreeAssembly,
    PartBatchTreeAssemblyResult,
    PartBatchTreeItem,
    PartBatchTreePartResult,
    PartBatchTreeRequest,
    PartBatchTreeResult,
    PartCreateRequest,
    PartEventOut,
    PartListItem,
    PartListOut,
    PartListQuery,
    PartOut,
    PartPickUpRequest,
    PartScanRequest,
    PartUpdateRequest,
    PlaceOnShelfRequest,
    ReceiveToInspectionRequest,
    SendToOutsourceRequest,
)
from schema.assembly import AssemblyOut
from schema._types import IdStr, IdStrNonNull
from schema.part_file import PartFileOut
from service._batch_ops import (
    ROLLUP_PROGRESS as _BATCH_ROLLUP_PROGRESS,
    TERMINAL_STATUSES as _BATCH_TERMINAL_STATUSES,
)
from service._id_parse import parse_snowflake_id
from service._session_refresh import refresh_for_state_machine
from service._customer_helpers import (  # 2026-07-28：抽到共享模块
    expand_customer_ids,
    make_customer_path_cached,
    preload_customer_cache,
)
from service.applicant import ApplicantService
from service.part_file import PartFileService
from core.time import now_naive
from utils.id_gen import new_id

Broadcaster = Callable[[], Awaitable[None]]
EventBroadcaster = Callable[[str, dict], Awaitable[None]]


def _item_to_part_create_request(item: PartBatchTreeItem) -> PartCreateRequest:
    """PartBatchTreeItem → PartCreateRequest。`create_parts_tree` 调单页 PDF 时复用 `create_part` 走标准路径。

    PR-H 2026-07-28：unit_price / total_price 直接从 item 透传（历史价确认单回填）。
    """
    return PartCreateRequest(
        name=item.name,
        drawing_no=item.drawing_no,
        applicant_name=item.applicant_name,
        applicant_id=item.applicant_id,
        quantity=item.quantity,
        unit_price=item.unit_price if item.unit_price is not None else Decimal("0"),
        total_price=item.total_price if item.total_price is not None else Decimal("0"),
        request_date=item.request_date,
        planned_delivery_date=item.planned_delivery_date,
        is_urgent=item.is_urgent,
        order_no=item.order_no,
        system_delivery_date=item.system_delivery_date,
        note=item.note,
        customer_id=item.customer_id,
    )


async def _maybe_upload_three_d_model(
    *,
    owner_id: int,
    item: "PartBatchTreeItem",
    three_d_payloads: dict[int, tuple[bytes, str, str | None]] | None,
    part_files: "PartFileService",
) -> None:
    """PR-H 2026-07-28：若 item.three_d_index 指向有效 three_d_models 槽位，则上传
    kind=THREE_D_MODEL 到 owner_id（t_part.id 或 t_assembly.id）。

    item.three_d_index 缺失 / 越界 / 槽位为空 → 静默跳过；该挂载是 best-effort，
    失败由 `part_files.upload` 内置 BizError 向上抛，事务回滚。
    """
    if item.three_d_index is None:
        return
    if not three_d_payloads:
        return
    payload = three_d_payloads.get(item.three_d_index)
    if payload is None:
        return
    raw, fname, ctype = payload
    from model.enums import PartFileKind
    await part_files.upload(
        owner_id=owner_id,
        kind=PartFileKind.THREE_D_MODEL,
        data=raw,
        original_filename=fname,
        content_type=ctype,
    )


def _parse_status(value: str | PartStatus | None) -> PartStatus | None:
    if value is None:
        return None
    if isinstance(value, PartStatus):
        return value
    try:
        return PartStatus(value)
    except ValueError as e:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"invalid part status: {value!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        ) from e


def _parse_event_type(value: str | PartEventType) -> PartEventType:
    if isinstance(value, PartEventType):
        return value
    try:
        return PartEventType(value)
    except ValueError as e:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"invalid event type: {value!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        ) from e


class PartService:
    def __init__(
        self,
        parts: PartRepository,
        customers: CustomerRepository,
        workers: WorkerRepository,
        events: PartEventRepository,
        serial_counters: SerialCounterRepository,
        shelves: ShelfRepository,
        processes: ProcessRepository | None = None,
        work_types: WorkTypeRepository | None = None,
        work_type_process: WorkTypeProcessRepository | None = None,
        applicants: ApplicantRepository | None = None,
        shelf_process_repo: ShelfProcessRepository | None = None,
        files: PartFileRepository | None = None,
        assemblies: "AssemblyRepository | None" = None,  # 2026-07-21：create_parts_tree 用
        delivery_notes_repo: DeliveryNoteRepository | None = None,  # 2026-07-22：PR-G 详情显示所属送货单
        outsource_companies: OutsourceCompanyRepository | None = None,
        outsource_company_process: OutsourceCompanyProcessRepository | None = None,
        outsource_quotes: OutsourceQuoteRepository | None = None,
        quote_events: OutsourceQuoteEventRepository | None = None,
        outsource_shipments: "OutsourceShipmentRepository | None" = None,  # 2026-07-30：外协发货记录
        part_batches: PartBatchRepository | None = None,  # 2026-07-29：批次化
        broadcaster: Broadcaster | None = None,
        event_broadcaster: EventBroadcaster | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.parts = parts
        self.part_batches = part_batches
        self.customers = customers
        self.workers = workers
        self.events = events
        self.serial_counters = serial_counters
        self.shelves = shelves
        self.processes = processes
        self.work_types = work_types
        self.work_type_process = work_type_process
        self.applicants = applicants  # 可选：用于根据 applicant_id 解析 applicant_name
        self.shelf_process_repo = shelf_process_repo  # 可选：用于放回时校验工序属于货架
        self.files = files  # 可选：批量新建零件时上传 PDF 图纸
        self.assemblies = assemblies  # 2026-07-21：可选：create_parts_tree 写 t_assembly
        self.delivery_notes_repo = delivery_notes_repo  # 2026-07-22：PR-G，可选：详情页显示所属送货单
        self.outsource_companies = outsource_companies  # 2026-07-15：外协公司（send_to_outsource 用）
        self.outsource_company_process = outsource_company_process  # 2026-07-15：外协公司-工序映射
        self.outsource_quotes = outsource_quotes  # 2026-07-16：外协报价（send_to_outsource 防御）
        self.quote_events = quote_events  # 2026-07-16：外协报价事件
        self.outsource_shipments = outsource_shipments  # 2026-07-30：外协发货记录
        self.broadcaster = broadcaster
        self.event_broadcaster = event_broadcaster
        self._current_user = current_user
        self._user_id: int | None = current_user.id if current_user else None
        self._username: str | None = current_user.username if current_user else None

    # ============================================================
    # 查询
    # ============================================================
    async def list_parts(self, query: PartListQuery) -> PartListOut:
        # customer_id 入参是雪花 ID 字符串，转 int。
        customer_id_int = parse_snowflake_id(query.customer_id, field_name="customer_id") if query.customer_id else None
        # 客户筛选级联：选 L1（一级集团）自动包含其下 L2 子客户；选 L2 叶子
        # 节点就是单 id。两种情形最终都通过 `customer_ids_in=[...]` 传给 repo，
        # 由 repo 用 `customer_id IN (...)` 一条 SQL 完成。
        customer_ids_in: list[int] | None = None
        if customer_id_int is not None:
            cust = await self.customers.get_by_id(customer_id_int)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {query.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if cust.parent_id is None:
                # L1 节点：扩展 = [self] + 全部子节点
                children = await self.customers.list_children(customer_id_int)
                customer_ids_in = [customer_id_int] + [c.id for c in children]
            else:
                # L2 叶子：单 id
                customer_ids_in = [customer_id_int]

        if not query.include_assemblies:
            # 原有行为（向后兼容）
            rows = await self.parts.list_with_filters(
                customer_ids_in=customer_ids_in,
                statuses=query.statuses,
                is_urgent=query.is_urgent,
                keyword=query.keyword,
                order_no=query.order_no,
                serial_no=query.serial_no,
                has_outsource_history=query.has_outsource_history,
                request_date_from=query.request_date_from,
                request_date_to=query.request_date_to,
                planned_delivery_date_from=query.planned_delivery_date_from,
                planned_delivery_date_to=query.planned_delivery_date_to,
                system_delivery_date_from=query.system_delivery_date_from,
                system_delivery_date_to=query.system_delivery_date_to,
                next_process_ids=query.next_process_ids,  # 2026-08-01
                locations=query.locations,  # 2026-08-01
                sort_by=query.sort_by,
                sort_dir=query.sort_dir,
                limit=query.limit,
                offset=query.offset,
            )
            total = await self.parts.count_with_filters(
                customer_ids_in=customer_ids_in,
                statuses=query.statuses,
                is_urgent=query.is_urgent,
                keyword=query.keyword,
                order_no=query.order_no,
                serial_no=query.serial_no,
                has_outsource_history=query.has_outsource_history,
                request_date_from=query.request_date_from,
                request_date_to=query.request_date_to,
                planned_delivery_date_from=query.planned_delivery_date_from,
                planned_delivery_date_to=query.planned_delivery_date_to,
                system_delivery_date_from=query.system_delivery_date_from,
                system_delivery_date_to=query.system_delivery_date_to,
                next_process_ids=query.next_process_ids,  # 2026-08-01
                locations=query.locations,  # 2026-08-01
            )
            items = await self._to_list_out(rows)
            return PartListOut(
                items=items, total=total, limit=query.limit, offset=query.offset
            )

        # ===== 装配体并入零件一览（2026-07-30）=====
        # 数据量工厂级（数百行），Python 内存合并分页；增长后应改 SQL UNION。
        # 1. 独立零件（排除装配件子件）
        part_rows = await self.parts.list_with_filters(
            customer_ids_in=customer_ids_in,
            statuses=query.statuses,
            is_urgent=query.is_urgent,
            keyword=query.keyword,
            order_no=query.order_no,
            serial_no=query.serial_no,
            has_outsource_history=query.has_outsource_history,
            request_date_from=query.request_date_from,
            request_date_to=query.request_date_to,
            planned_delivery_date_from=query.planned_delivery_date_from,
            planned_delivery_date_to=query.planned_delivery_date_to,
            system_delivery_date_from=query.system_delivery_date_from,
            system_delivery_date_to=query.system_delivery_date_to,
            next_process_ids=query.next_process_ids,  # 2026-08-01
            locations=query.locations,  # 2026-08-01
            sort_by=query.sort_by,
            sort_dir=query.sort_dir,
            assembly_id_is_null=True,
            limit=query.limit + query.offset,
            offset=0,
        )
        part_total = await self.parts.count_with_filters(
            customer_ids_in=customer_ids_in,
            statuses=query.statuses,
            is_urgent=query.is_urgent,
            keyword=query.keyword,
            order_no=query.order_no,
            serial_no=query.serial_no,
            has_outsource_history=query.has_outsource_history,
            request_date_from=query.request_date_from,
            request_date_to=query.request_date_to,
            planned_delivery_date_from=query.planned_delivery_date_from,
            planned_delivery_date_to=query.planned_delivery_date_to,
            system_delivery_date_from=query.system_delivery_date_from,
            system_delivery_date_to=query.system_delivery_date_to,
            next_process_ids=query.next_process_ids,  # 2026-08-01
            locations=query.locations,  # 2026-08-01
            assembly_id_is_null=True,
        )

        # 2. 装配件（statuses 取交集）
        # 2026-07-31：装配件本身不外协（外协走 t_part），所以 has_outsource_history
        # 开启时直接跳过整个装配体查询块。
        # 2026-08-01：装配件没有 next_process_id / part.location，故 next_process_ids /
        # locations 任一非空时也直接跳过 asm_rows（合并结果里不出现装配行）。
        asm_rows: list[TAssembly] = []
        asm_total = 0
        if (
            self.assemblies is not None
            and not query.has_outsource_history
            and not query.next_process_ids
            and not query.locations
        ):
            assembly_statuses = None
            if query.statuses is not None:
                # 2026-08-03：装配件状态扩到 7 态，过滤白名单跟随枚举自动追踪
                from model.enums import AssemblyStatus
                valid_asm_statuses = {s.value for s in AssemblyStatus}
                assembly_statuses = [s.value for s in query.statuses if s.value in valid_asm_statuses]
                if not assembly_statuses:
                    # 无匹配装配件状态，装配件集为空
                    asm_rows = []
                    asm_total = 0
            if query.statuses is None or assembly_statuses:
                from repository.assembly import AssemblySortKey
                _sort_key_map = {
                    PartSortKey.PLANNED_DELIVERY_DATE: AssemblySortKey.PLANNED_DELIVERY_DATE,
                    PartSortKey.REQUEST_DATE: AssemblySortKey.REQUEST_DATE,
                    PartSortKey.CREATED_AT: AssemblySortKey.CREATED_AT,
                    PartSortKey.SERIAL_NO: AssemblySortKey.SERIAL_NO,
                    PartSortKey.DRAWING_NO: AssemblySortKey.DRAWING_NO,
                    PartSortKey.NAME: AssemblySortKey.NAME,
                    PartSortKey.QUANTITY: AssemblySortKey.QUANTITY,  # 2026-08-01
                    PartSortKey.UNIT_PRICE: AssemblySortKey.UNIT_PRICE,  # 2026-08-01
                    PartSortKey.TOTAL_PRICE: AssemblySortKey.TOTAL_PRICE,  # 2026-08-01
                }
                asm_sort_by = _sort_key_map.get(query.sort_by, AssemblySortKey.PLANNED_DELIVERY_DATE)
                asm_rows = await self.assemblies.list_with_filters(
                    customer_ids_in=customer_ids_in,
                    statuses=assembly_statuses,
                    is_urgent=query.is_urgent,
                    drawing_no_like=query.keyword,
                    name_like=query.keyword,
                    order_no_like=query.order_no,
                    serial_no_like=query.serial_no,
                    request_date_from=query.request_date_from,
                    request_date_to=query.request_date_to,
                    planned_delivery_date_from=query.planned_delivery_date_from,
                    planned_delivery_date_to=query.planned_delivery_date_to,
                    system_delivery_date_from=query.system_delivery_date_from,
                    system_delivery_date_to=query.system_delivery_date_to,
                    sort_by=asm_sort_by,
                    sort_dir=query.sort_dir.value,
                    limit=query.limit + query.offset,
                    offset=0,
                )
                asm_total = await self.assemblies.count_with_filters(
                    customer_ids_in=customer_ids_in,
                    statuses=assembly_statuses,
                    is_urgent=query.is_urgent,
                    drawing_no_like=query.keyword,
                    name_like=query.keyword,
                    order_no_like=query.order_no,
                    serial_no_like=query.serial_no,
                    request_date_from=query.request_date_from,
                    request_date_to=query.request_date_to,
                    planned_delivery_date_from=query.planned_delivery_date_from,
                    planned_delivery_date_to=query.planned_delivery_date_to,
                    system_delivery_date_from=query.system_delivery_date_from,
                    system_delivery_date_to=query.system_delivery_date_to,
                )

        # 3. 转换并合并
        part_items = await self._to_list_out(part_rows)
        for item in part_items:
            item.row_type = "PART"
        asm_items = await self._assemblies_to_list_items(asm_rows)
        merged = part_items + asm_items

        # 4. 统一排序（Python 内存）
        _nulls_last_keys = {PartSortKey.SYSTEM_DELIVERY_DATE, PartSortKey.ORDER_NO}

        def _sort_key(item: PartListItem, *, nulls_first: bool = False) -> tuple:
            none_flag = 0 if nulls_first else 1
            non_none_flag = 1 if nulls_first else 0
            if query.sort_by == PartSortKey.PLANNED_DELIVERY_DATE:
                val = item.planned_delivery_date
                return (non_none_flag, val) if val is not None else (none_flag, date.min)
            elif query.sort_by == PartSortKey.REQUEST_DATE:
                val = item.request_date
                return (non_none_flag, val) if val is not None else (none_flag, date.min)
            elif query.sort_by == PartSortKey.SYSTEM_DELIVERY_DATE:
                val = item.system_delivery_date
                return (non_none_flag, val) if val is not None else (none_flag, date.min)
            elif query.sort_by == PartSortKey.CREATED_AT:
                val = item.created_at
                return (non_none_flag, val) if val is not None else (none_flag, datetime.min)
            elif query.sort_by == PartSortKey.SERIAL_NO:
                val = item.serial_no
                return (non_none_flag, val) if val is not None else (none_flag, "")
            elif query.sort_by == PartSortKey.DRAWING_NO:
                val = item.drawing_no
                return (non_none_flag, val) if val is not None else (none_flag, "")
            elif query.sort_by == PartSortKey.NAME:
                val = item.name
                return (non_none_flag, val) if val is not None else (none_flag, "")
            elif query.sort_by == PartSortKey.ORDER_NO:
                val = item.order_no
                return (non_none_flag, val) if val is not None else (none_flag, "")
            elif query.sort_by == PartSortKey.QUANTITY:
                val = item.quantity
                # 数量列非 NULL（default 1 / server_default 1），无需 None 兜底
                return (non_none_flag, val if val is not None else 0)
            elif query.sort_by == PartSortKey.UNIT_PRICE:
                val = item.unit_price
                return (non_none_flag, val if val is not None else Decimal("0"))
            elif query.sort_by == PartSortKey.TOTAL_PRICE:
                val = item.total_price
                return (non_none_flag, val if val is not None else Decimal("0"))
            return (non_none_flag, "")

        # 先按 id DESC 稳定排序（保证 tie-break 与 SQL 一致）
        merged = sorted(merged, key=lambda x: x.id, reverse=True)
        if query.sort_dir == SortDir.ASC:
            merged = sorted(merged, key=lambda x: _sort_key(x, nulls_first=False))
        else:
            if query.sort_by in _nulls_last_keys:
                merged = sorted(merged, key=lambda x: _sort_key(x, nulls_first=False), reverse=True)
            else:
                merged = sorted(merged, key=lambda x: _sort_key(x, nulls_first=True), reverse=True)

        # 5. 内存分页
        items = merged[query.offset:query.offset + query.limit]
        total = part_total + asm_total
        return PartListOut(
            items=items, total=total, limit=query.limit, offset=query.offset
        )

    async def get_part(self, part_id: int) -> PartOut:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        items = await self._to_out([part])
        return items[0]

    async def list_events(self, part_id: int) -> list[PartEventOut]:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        events = await self.events.list_by_part(part_id)
        # 批次号映射（2026-07-29）：事件带 batch_id，时间线展示「批次N」。
        batch_ids = {e.batch_id for e in events if e.batch_id}
        batch_no_map: dict[int, int] = {}
        if batch_ids and self.part_batches is not None:
            for b in await self.part_batches.list_by_part(part_id):
                if b.id in batch_ids:
                    batch_no_map[b.id] = b.batch_no
        worker_ids = {e.worker_id for e in events if e.worker_id}
        worker_map: dict[int, TWorker] = {}
        if worker_ids:
            workers = await self.workers.list_with_filters(
                is_active=None, limit=max(100, len(worker_ids))
            )
            worker_map = {w.id: w for w in workers if w.id in worker_ids}
        # operator_username / operator_name：通过 t_user 现算（model 不冗余，避免
        # N 行事件 N 次 JOIN 单独建索引的代价；list_events 一次性查整本批 map）。
        operator_ids = {e.created_by for e in events if e.created_by}
        user_map: dict[int, str] = {}
        user_name_map: dict[int, str] = {}
        if operator_ids:
            from model import TUser
            from sqlalchemy import select as _sa_select
            user_rows = await self.workers.session.execute(
                _sa_select(TUser.id, TUser.username, TUser.full_name).where(
                    TUser.id.in_(operator_ids)
                )
            )
            for uid, uname, fname in user_rows.all():
                user_map[int(uid)] = uname
                user_name_map[int(uid)] = fname or uname  # full_name 空时回退 username
        return [
            PartEventOut(
                id=e.id,
                part_id=e.part_id,
                batch_id=e.batch_id,
                batch_no=batch_no_map.get(e.batch_id) if e.batch_id else None,
                quantity=e.quantity,
                worker_id=e.worker_id,
                worker_name=worker_map[e.worker_id].name
                if e.worker_id in worker_map
                else None,
                event_type=e.event_type,
                from_status=e.from_status,
                to_status=e.to_status,
                drawing_code=e.drawing_code,
                badge_code=e.badge_code,
                note=e.note,
                created_by=e.created_by,
                operator_username=user_map.get(e.created_by) if e.created_by else None,
                # 2026-07-17：历史记录中显示操作者姓名（username 仍保留供后端 audit 用）
                operator_name=user_name_map.get(e.created_by) if e.created_by else None,
                created_at=e.created_at,
            )
            for e in events
        ]

    async def _write_event(
        self,
        *,
        part: TPart,
        event_type: PartEventType,
        from_status: PartStatus | None,
        to_status: PartStatus | None,
        worker_id: int | None = None,
        drawing_code: str | None = None,
        badge_code: str | None = None,
        note: str | None = None,
        created_by: int | None = None,
    ) -> TPartEvent:
        event = TPartEvent(
            id=new_id(),
            part_id=part.id,
            worker_id=worker_id,
            event_type=event_type.value,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value if to_status else None,
            drawing_code=drawing_code,
            badge_code=badge_code,
            note=note,
            created_by=created_by,
        )
        return await self.events.create(event)

    async def _broadcast(self) -> None:
        if self.broadcaster is None:
            return
        try:
            await self.broadcaster()
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("dashboard broadcast failed")

    async def _broadcast_event(self, event_type: str, payload: dict) -> None:
        if self.event_broadcaster is None:
            return
        try:
            await self.event_broadcaster(event_type, payload)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "dashboard event broadcast failed: %s", event_type
            )

    @staticmethod
    def _banner_payload(
        part: TPart,
        *,
        customer_path: str | None,
        worker_name: str | None = None,
        shelf_code: str | None = None,
    ) -> dict:
        return {
            "serial_no": part.serial_no,
            "drawing_no": part.drawing_no,
            "name": part.name,
            "customer_path": customer_path,
            "is_urgent": bool(part.is_urgent),
            "planned_delivery_date": part.planned_delivery_date.isoformat()
            if part.planned_delivery_date
            else None,
            "worker_name": worker_name,
            "shelf_code": shelf_code,
        }

    # ============================================================
    # 批次原语（2026-07-29 批次化：拆分 / 解析 / rollup）
    # ============================================================

    # 工单 rollup 进度序 / 终态集合：与 service/_batch_ops.py 共享一份。
    _ROLLUP_PROGRESS = _BATCH_ROLLUP_PROGRESS
    _TERMINAL_STATUSES = _BATCH_TERMINAL_STATUSES

    def _batches(self) -> PartBatchRepository:
        if self.part_batches is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing part batch repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return self.part_batches

    async def _split_batch(
        self, part: TPart, batch: TPartBatch, qty: int,
    ) -> TPartBatch:
        """从 `batch` 拆出 `qty` 件为新批次（继承源批次状态/位置/holder/工序）。

        - 并发：`get_for_update`（FOR UPDATE + populate_existing）锁源批次行，
          同事单的拆分串行化；锁内重校验数量边界。
        - batch_no：锁内 MAX+1，保证 (part_id, batch_no) 唯一。
        - 新批次 **不继承** delivery_note_id（源批次若在送货单上，拆出的量
          默认不跟单；要跟单走送货单 add_parts 重新挂）。
        - 写 SPLIT 事件（挂在新批次上，quantity=拆出量）。
        """
        from service._batch_ops import split_batch as _split
        return await _split(
            batches=self._batches(), events=self.events,
            part=part, batch=batch, qty=qty, user_id=self._user_id,
        )

    async def _maybe_split(
        self, part: TPart, batch: TPartBatch, quantity: int | None,
    ) -> TPartBatch:
        """部分量入口：quantity 缺省 / 等于批次量 → 原批次；否则先拆再返回新批次。"""
        if quantity is None:
            return batch
        if quantity <= 0 or quantity > batch.quantity:
            raise BizError(
                code=ErrCode.BIZ_PART_BATCH_INVALID_QUANTITY,
                message=(
                    f"操作数量必须 ∈ [1, {batch.quantity}]"
                    f"（批次 {batch.batch_no} 当前 {batch.quantity} 件），got {quantity}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if quantity == batch.quantity:
            return batch
        return await self._split_batch(part, batch, quantity)

    async def _resolve_target_batch(
        self,
        part: TPart,
        batch_id: int | None,
        *,
        expect: "Callable[[TPartBatch], bool] | None" = None,
        action: str = "操作",
    ) -> TPartBatch:
        """解析本次流转的目标批次。

        - `batch_id` 显式指定 → 校验属于该工单；
        - 缺省 → 在 `expect` 谓词筛出的候选里取唯一者；
          0 个 → BIZ_INVALID_TRANSITION；>1 个 → 400 要求指定 batch_id。
        """
        batches = await self._batches().list_by_part(part.id)
        if batch_id is not None:
            target = next((b for b in batches if b.id == batch_id), None)
            if target is None:
                raise BizError(
                    code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
                    message=f"batch {batch_id} 不属于工单 {part.id} 或不存在",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            target._part_serial = part.serial_no  # transient：SM 事件 drawing_code 用
            return target
        candidates = [b for b in batches if expect(b)] if expect else batches
        if not candidates:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=f"{action}：工单 {part.id} 当前没有可操作的批次",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if len(candidates) > 1:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"{action}：工单有 {len(candidates)} 个可操作批次"
                    f"（{', '.join(f'批次{b.batch_no}:{b.quantity}件' for b in candidates)}），"
                    "请指定 batch_id"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        return candidates[0]

    async def _rollup_part_status(self, part: TPart) -> None:
        """批次流转后重算工单派生状态（就地改 part 字段；caller 负责 update/flush）。

        规则：
        - 有活跃批次 → part.status/location/holder/next_process_id =
          「最落后」活跃批次的同名字段（进度序见 _ROLLUP_PROGRESS，同级取 batch_no 小者）。
        - 全部终态 → 全部 CANCELLED ⇒ CANCELLED，否则 COMPLETED；
          释放 serial_no（回池），写工单级终态事件（batch_id=NULL）。
        - 全部活跃批次都已 DELIVERED（或更后）且 actual_delivery_date 未填 → 记今天。
        """
        from service._batch_ops import rollup_part_status as _rollup
        await _rollup(
            batches=self._batches(), events=self.events,
            part=part, user_id=self._user_id,
        )

    async def _after_batch_transition(self, part: TPart) -> None:
        """批次流转统一收尾：rollup → part update → 看板广播 → 装配体联动。

        rollup 前显式刷新派生字段：审计列 onupdate / 测试 hook 可能把
        status/location 等标 expired，rollup 的 sync 读（prev_status /
        actual_delivery_date）在 async session 会触发 MissingGreenlet
        （CLAUDE.md §MissingGreenlet；反例见 receive_to_inspection auto_pass 链路）。
        """
        await refresh_for_state_machine(
            self.parts.session, part,
            attrs=(
                "status", "location", "current_holder_id",
                "next_process_id", "serial_no", "actual_delivery_date",
            ),
        )
        await self._rollup_part_status(part)
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)

    async def create_root_batch(self, part: TPart) -> TPartBatch:
        """新工单建根批次（batch_no=1，quantity=工单总量，状态跟随 part）。"""
        root = TPartBatch(
            id=new_id(),
            part_id=part.id,
            batch_no=1,
            quantity=part.quantity,
            status=part.status,
            location=part.location,
            current_holder_id=part.current_holder_id,
            next_process_id=part.next_process_id,
            placed_at=part.placed_at,
        )
        root.created_by = self._user_id
        root.updated_by = self._user_id
        await self._batches().create(root)
        return root

    # ============================================================
    # 写操作
    # ============================================================
    async def create_part(self, data: PartCreateRequest) -> PartOut:
        # customer_id 入参是雪花 ID 字符串（CLAUDE.md §3），转回 int。
        customer_id_int = parse_snowflake_id(data.customer_id, field_name="customer_id")
        if customer_id_int is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {data.customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        cust = await self.customers.get_by_id(customer_id_int)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {data.customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is not None:
            # 二级客户 → 用一级父客户的 serial_prefix 派生前缀
            parent = await self.customers.get_by_id(cust.parent_id)
            if parent is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"parent customer {cust.parent_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            root_customer = parent
            root_customer_id = parent.id
        else:
            # 一级客户自身
            root_customer = cust
            root_customer_id = cust.id
        # 解析前缀（DB 列优先；PARENT_TO_CODE 兜底；都无 → 400）。
        code = resolve_root_prefix(root_customer)
        # 解析 applicant_id → applicant_name（按姓名快照写入 t_part）
        # 注：applicant_id 在 schema 是 str（雪花 ID 字符串，避免 JS Number 精度丢失），
        # 这里转回 int 再去 repository 查询。
        applicant_name = data.applicant_name
        if data.applicant_id is not None:
            if self.applicants is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="server missing applicant repository",
                    http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            applicant_id_int = parse_snowflake_id(
                data.applicant_id, field_name="applicant_id",
            )
            if applicant_id_int is None:
                raise BizError(
                    code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                    message=f"applicant {data.applicant_id!r} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            applicant = await self.applicants.get_by_id(applicant_id_int)
            if applicant is None:
                raise BizError(
                    code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                    message=f"applicant {data.applicant_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if applicant.customer_id != root_customer_id:
                raise BizError(
                    code=ErrCode.BIZ_APPLICANT_BAD_CUSTOMER,
                    message=(
                        f"applicant {data.applicant_id} 不属于本零件的一级客户 "
                        f"{root_customer_id}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            applicant_name = applicant.name
        serial_no = await self.serial_counters.acquire_serial(code)
        total_price = data.total_price
        if total_price is None:
            total_price = data.unit_price * data.quantity

        # data.customer_id 是雪花 ID 字符串，TPart.customer_id 是 BigInteger → int
        assert customer_id_int is not None  # 前段已校验
        part = TPart(
            id=new_id(),
            serial_no=serial_no,
            name=data.name,
            drawing_no=data.drawing_no,
            applicant_name=applicant_name,
            quantity=data.quantity,
            unit_price=data.unit_price,
            total_price=total_price,
            request_date=data.request_date,
            planned_delivery_date=data.planned_delivery_date,
            actual_delivery_date=data.actual_delivery_date,
            status=PartStatus.PENDING.value,
            is_urgent=data.is_urgent,
            order_no=data.order_no,
            system_delivery_date=data.system_delivery_date,
            note=data.note,
            customer_id=customer_id_int,
        )
        part.location = "OFFICE"
        part.created_by = self._user_id
        part.updated_by = self._user_id
        await self.parts.create(part)
        # 2026-07-29 批次化：每个工单创建时生成根批次（batch_no=1，qty=总量）。
        root_batch = await self.create_root_batch(part)
        event = TPartEvent(
            id=new_id(),
            part_id=part.id,
            batch_id=root_batch.id,
            worker_id=None,
            event_type=PartEventType.CREATED.value,
            from_status=None,
            to_status=PartStatus.PENDING.value,
            drawing_code=None,
            badge_code=None,
            note=None,
            quantity=part.quantity,
            created_by=self._user_id,
        )
        await self.events.create(event)
        items = await self._to_out([part])
        return items[0]

    async def create_parts_batch(
        self,
        payload: PartBatchCreateRequest,
        *,
        file_payloads: list[tuple[bytes, str, str | None] | None] | None = None,
    ) -> PartBatchCreateResult:
        """批量新建零件；可选地按 items 下标对齐上传 PDF 图纸。

        `file_payloads[i]` 是 `items[i]` 的 PDF（`(bytes, filename, content_type)`）；
        `None` 表示该行无图纸。前端可传比 items 短的文件列表（按 None 补齐），
        也可不传（`file_payloads=None`，全部按无图纸处理）。

        事务边界：与 caller 共享同一 session/事务（由 `api/deps.get_session`
        在请求结束 commit）。任一 PDF 上传失败 → BizError 上抛 → 整批回滚，
        已 flush 的 t_part / t_part_file 行全部丢失；COS 孤儿由 helper
        fire-and-forget 兜底清理。
        """
        # 若前端传了任何 PDF 但 service 未注入 files repo，是配置错误：
        # 不要把 t_part 行悄悄建好却不挂图，导致「零件已建但图丢了」的不一致。
        if file_payloads and any(fp is not None for fp in file_payloads):
            if self.files is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="server missing part file repository",
                    http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        customer_cache: dict[int, TCustomer | None] = {}
        parent_cache: dict[int, TCustomer | None] = {}
        # 缓存键改为 root_customer_id（int），闭包内部调 resolve_root_prefix 取前缀。
        # 缓存键改为 root_customer_id（int），闭包内部调 resolve_root_prefix 取前缀。
        # 避免对同一 root 重复 dict 查找；resolve_root_prefix 自身已有 PARENT_TO_CODE 兜底。
        prefix_cache: dict[int, str] = {}

        async def get_customer(cid: int) -> TCustomer | None:
            if cid not in customer_cache:
                customer_cache[cid] = await self.customers.get_by_id(cid)
            return customer_cache[cid]

        async def get_parent(pid: int) -> TCustomer | None:
            if pid not in parent_cache:
                parent_cache[pid] = await self.customers.get_by_id(pid)
            return parent_cache[pid]

        def get_prefix_for_root(root: TCustomer) -> str:
            if root.id in prefix_cache:
                return prefix_cache[root.id]
            prefix = resolve_root_prefix(root)
            prefix_cache[root.id] = prefix
            return prefix

        failed: list[PartBatchCreateItemFailure] = []
        for idx, item in enumerate(payload.items):
            # item.customer_id 是雪花 ID 字符串，转 int
            item_cid = parse_snowflake_id(item.customer_id, field_name="customer_id")
            if item_cid is None:
                failed.append(PartBatchCreateItemFailure(
                    index=idx, message=f"customer {item.customer_id!r} not found",
                ))
                continue
            cust = await get_customer(item_cid)
            if cust is None:
                failed.append(PartBatchCreateItemFailure(index=idx, message=f"customer {item.customer_id!r} not found"))
                continue
            if cust.parent_id is not None:
                parent = await get_parent(cust.parent_id)
                if parent is None:
                    failed.append(PartBatchCreateItemFailure(index=idx, message=f"parent customer {cust.parent_id} not found"))
                    continue
                root_customer = parent
            else:
                root_customer = cust
            try:
                get_prefix_for_root(root_customer)
            except BizError as exc:
                # 包装成 failed 条目；message 与旧版「未配置客户...序列号代码」对齐
                # 以保证旧测试的 substring 断言仍命中。
                failed.append(PartBatchCreateItemFailure(
                    index=idx,
                    message=f"未配置客户「{root_customer.name}」的序列号代码",
                ))
                # 用 exc 的 message 字段校验 root.name 是否真的在 helper message 里
                # （仅 debug 价值，不影响主流程）
                assert root_customer.name in exc.message

        if failed:
            return PartBatchCreateResult(created=[], failed=failed)

        created: list[PartOut] = []
        for idx, item in enumerate(payload.items):
            out = await self.create_part(item)
            # file_payloads 与 items 按下标对齐：fp 非 None 才上传 PDF。
            # `out.id` 是 IdStrNonNull，Python 内部仍是 int（schema/_types.py）。
            if file_payloads and idx < len(file_payloads):
                fp = file_payloads[idx]
                if fp is not None:
                    file_data, filename, content_type = fp
                    # 2026-07-10 起：统一文件表，单文件 kind=DRAWING 自动覆盖
                    from model.enums import PartFileKind
                    _part_files = PartFileService(files=self.files, current_user=self._current_user)
                    await _part_files.upload(
                        owner_id=out.id,
                        kind=PartFileKind.DRAWING,
                        data=file_data,
                        original_filename=filename,
                        content_type=content_type,
                    )
            created.append(out)
        return PartBatchCreateResult(created=created, failed=[])

    async def create_parts_tree(
        self,
        payload: PartBatchTreeRequest,
        *,
        file_payloads_by_pdf_index: dict[int, tuple[bytes, str, str | None]],
        three_d_payloads_by_index: (
            dict[int, tuple[bytes, str, str | None]] | None
        ) = None,
        part_files: PartFileService,
        applicants: ApplicantService,
    ) -> PartBatchTreeResult:
        """批量树形创建：单页 PDF → 独立零件；多页 PDF → 装配件 + 子件。

        流程：
          1. 前置校验：customer / 多页约束 / master 数量；失败 → `failed` 列表，0 写盘。
          2. Excel 申请人兜底：applicant_id 缺 + name 非空 → bulk_get_or_create 回填。
          3. 主循环（按 pdf_index）：
             - 单页 → create_part + DRAWING 上传 + 若 three_d_index 非空 → THREE_D_MODEL 上传；
             - 多页 → acquire serial + 写 t_assembly + 逐 page 拆 + 写子件 + DRAWING 上传 +
               若对应 page.three_d_index 非空 → THREE_D_MODEL 上传 +
               若有 master_item → ASSEMBLY_MASTER 上传。
          4. 事务边界：与 caller 共享 session；任一 upload 抛 BizError → 整批回滚。

        2026-07-21 新增；PR-H 2026-07-28 加 three_d_payloads_by_index。
        `POST /parts/batch-with-pdfs` 调用本方法。
        """
        if self.assemblies is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing assembly repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ===== 1. 前置校验 =====
        from collections import defaultdict
        items_by_pdf: dict[int, list[PartBatchTreeItem]] = defaultdict(list)
        for it in payload.items:
            items_by_pdf[it.pdf_index].append(it)

        failed: list[PartBatchCreateItemFailure] = []

        # 校验：items 数量必须与 PDF 文件对齐（每个 pdf_index 至少有对应文件）
        for pdf_index in items_by_pdf.keys():
            if pdf_index not in file_payloads_by_pdf_index:
                failed.append(PartBatchCreateItemFailure(
                    index=pdf_index,
                    message=f"未找到 pdf_index={pdf_index} 对应的 PDF 文件",
                ))

        # 校验：customer_id 存在 + 必须是叶子节点
        customer_cache: dict[int, TCustomer | None] = {}
        parent_cache: dict[int, TCustomer | None] = {}
        all_customer_ids: set[str] = set()
        all_customer_ids.update(it.customer_id for it in payload.items)
        all_customer_ids.update(a.customer_id for a in payload.assemblies)
        for cid_str in all_customer_ids:
            cid_int = parse_snowflake_id(cid_str, field_name="customer_id")
            if cid_int is None:
                failed.append(PartBatchCreateItemFailure(
                    index=0, message=f"customer {cid_str!r} not found",
                ))
                continue
            if cid_int not in customer_cache:
                customer_cache[cid_int] = await self.customers.get_by_id(cid_int)
            cust = customer_cache[cid_int]
            if cust is None:
                failed.append(PartBatchCreateItemFailure(
                    index=0, message=f"customer {cid_str!r} not found",
                ))
                continue
            if cust.parent_id is None:
                failed.append(PartBatchCreateItemFailure(
                    index=0,
                    message=(
                        f"customer {cid_str!r} 是一级客户（无 parent_id），"
                        "请选二级叶子节点"
                    ),
                ))

        # 校验：多页 PDF 约束 + master 数量 + assembly_uid 必填
        asm_by_uid: dict[str, PartBatchTreeAssembly] = {
            a.uid: a for a in payload.assemblies
        }
        for pdf_index, pages in items_by_pdf.items():
            if len(pages) > 1:
                # 多页：必须是相同 assembly_uid
                uids = {p.assembly_uid for p in pages}
                if len(uids) != 1 or None in uids:
                    failed.append(PartBatchCreateItemFailure(
                        index=pdf_index,
                        message=f"PDF {pdf_index} 多页但 assembly_uid 不一致",
                    ))
                    continue
                asm_uid = pages[0].assembly_uid
                if asm_uid not in asm_by_uid:
                    failed.append(PartBatchCreateItemFailure(
                        index=pdf_index,
                        message=f"PDF {pdf_index} 的 assembly_uid={asm_uid} 未在 assemblies 中找到",
                    ))
                    continue
                master_count = sum(1 for p in pages if p.is_master)
                if master_count > 1:
                    failed.append(PartBatchCreateItemFailure(
                        index=pdf_index,
                        message=f"PDF {pdf_index} 有 {master_count} 个 is_master=true，至多 1 个",
                    ))
                    continue
                if len(pages) > 100:
                    failed.append(PartBatchCreateItemFailure(
                        index=pdf_index,
                        message=f"PDF {pdf_index} 拆出 {len(pages)} 页 > 100",
                    ))
                    continue

        if failed:
            return PartBatchTreeResult.model_construct(
                standalone_parts=[], assemblies=[], failed=failed,
            )

        # ===== 2. 申请人兜底（按 L1 根去重 bulk） =====
        # 注意：前端 Excel 模式已经预调过 bulkGetOrCreateApplicants；此处只兜底漏网。
        bulk_items_map: dict[tuple[str, int], None] = {}  # (name, l1_root_id) -> None (占位)
        for it in payload.items:
            if it.applicant_name and not it.applicant_id:
                cid_int = parse_snowflake_id(it.customer_id, field_name="customer_id")
                cust = customer_cache[cid_int]
                parent = cust.parent_id
                l1_root_id = parent if parent else cust.id
                # 解析 L1
                if parent:
                    if parent not in parent_cache:
                        parent_cache[parent] = await self.customers.get_by_id(parent)
                    l1_root_id = parent_cache[parent].id if parent_cache[parent] else cust.id
                bulk_items_map.setdefault((it.applicant_name, l1_root_id), None)

        if bulk_items_map:
            from schema.applicant import BulkApplicantItem
            bulk_items = [
                BulkApplicantItem(name=name, customer_id=str(l1_root_id))
                for (name, l1_root_id) in bulk_items_map.keys()
            ]
            await applicants.bulk_get_or_create(bulk_items)

        # ===== 3. 主循环（按 pdf_index 顺序） =====
        from model.enums import PartFileKind, PartStatus
        from utils.pdf import split_pdf as _split_pdf

        standalone_results: list[PartBatchTreePartResult] = []
        assembly_results: list[PartBatchTreeAssemblyResult] = []

        for pdf_index, pages in items_by_pdf.items():
            pdf_bytes, fname, ctype = file_payloads_by_pdf_index[pdf_index]

            if len(pages) == 1:
                # === 单页 → 独立零件 ===
                page = pages[0]
                part_out = await self.create_part(_item_to_part_create_request(page))
                # 上传 DRAWING
                await part_files.upload(
                    owner_id=part_out.id,
                    kind=PartFileKind.DRAWING,
                    data=pdf_bytes,
                    original_filename=fname,
                    content_type=ctype,
                )
                # PR-H 2026-07-28：若挂 3D 模型则一并上传
                await _maybe_upload_three_d_model(
                    owner_id=part_out.id,
                    item=page,
                    three_d_payloads=three_d_payloads_by_index,
                    part_files=part_files,
                )
                # 写 CREATED 事件（create_part 内部已写，此处无需重复）
                # broadcast PART_CREATED
                if self.event_broadcaster:
                    try:
                        await self.event_broadcaster("PART_CREATED", {
                            "part_id": part_out.id,
                            "drawing_no": part_out.drawing_no,
                            "name": part_out.name,
                        })
                    except Exception:  # noqa: BLE001
                        pass
                standalone_results.append(PartBatchTreePartResult(
                    uid=f"single-{pdf_index}",
                    kind="part",
                    part=part_out,
                ))
                continue

            # === 多页 → 装配件 + 子件 ===
            asm_meta = asm_by_uid[pages[0].assembly_uid]
            cid_int = parse_snowflake_id(asm_meta.customer_id, field_name="customer_id")
            cust = customer_cache[cid_int]
            parent = cust.parent_id
            root_customer = parent_cache[parent] if parent else cust
            if parent and root_customer is None:
                root_customer = await self.customers.get_by_id(parent)
            code = resolve_root_prefix(root_customer)
            assembly_serial = await self.serial_counters.acquire_serial(code)

            master_pages = [p for p in pages if p.is_master]
            master_page = master_pages[0] if master_pages else None
            asm_drawing_no = (
                (master_page.drawing_no if master_page else None)
                or asm_meta.drawing_no
                or f"BATCH-{pdf_index:03d}"
            )
            asm_name = (
                (master_page.name if master_page else None)
                or asm_meta.name
                or f"批量装配件{pdf_index}"
            )

            asm = TAssembly(
                id=new_id(),
                drawing_no=asm_drawing_no,
                name=asm_name,
                applicant_name=(master_page.applicant_name if master_page else None)
                or asm_meta.applicant_name,
                customer_id=cid_int,
                request_date=asm_meta.request_date,
                planned_delivery_date=asm_meta.planned_delivery_date,
                actual_delivery_date=None,
                is_urgent=asm_meta.is_urgent,
                status=PartStatus.PENDING.value,
                serial_no=assembly_serial,
                quantity=asm_meta.quantity,
            )
            if self._user_id is not None:
                asm.created_by = self._user_id
                asm.updated_by = self._user_id
            await self.assemblies.create(asm)

            # 拆分 PDF
            try:
                all_pages = _split_pdf(pdf_bytes)
            except Exception as exc:
                raise BizError(
                    code=ErrCode.BIZ_PART_FILE_UPLOAD_FAILED,
                    message=f"PDF {pdf_index} 解析失败：{exc}",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                ) from exc

            child_results: list[PartBatchTreePartResult] = []
            child_files: list = []
            child_seq = 0
            for page in pages:
                if page.page_index >= len(all_pages):
                    raise BizError(
                        code=ErrCode.BIZ_INVALID_VALUE,
                        message=(
                            f"PDF {pdf_index} 仅 {len(all_pages)} 页，"
                            f"但 page_index={page.page_index}"
                        ),
                        http_status=http_status.HTTP_400_BAD_REQUEST,
                    )
                # 总装图页只在下方作为 ASSEMBLY_MASTER 上传，不建子件（2026-07-22）
                if page.is_master:
                    continue
                child_seq += 1
                page_bytes = all_pages[page.page_index]
                child_id = new_id()
                child_serial = f"{assembly_serial}-{child_seq:02d}"
                tpart = TPart(
                    id=child_id,
                    serial_no=child_serial,
                    name=page.name,
                    drawing_no=page.drawing_no,
                    applicant_name=(
                        page.applicant_name or asm_meta.applicant_name or "(未知)"
                    ),
                    quantity=page.quantity,
                    # PR-H 2026-07-28：单价 / 总价直接透传（来自历史价确认单）
                    unit_price=(
                        page.unit_price if page.unit_price is not None else Decimal("0")
                    ),
                    total_price=(
                        page.total_price if page.total_price is not None else Decimal("0")
                    ),
                    request_date=asm_meta.request_date,
                    planned_delivery_date=(
                        page.planned_delivery_date or asm_meta.planned_delivery_date
                    ),
                    actual_delivery_date=None,
                    order_no=page.order_no,
                    system_delivery_date=page.system_delivery_date,
                    note=page.note,
                    is_urgent=(page.is_urgent or asm_meta.is_urgent),
                    customer_id=cid_int,
                    assembly_id=asm.id,
                    status=PartStatus.PENDING.value,
                )
                if self._user_id is not None:
                    tpart.created_by = self._user_id
                    tpart.updated_by = self._user_id
                await self.parts.create(tpart)
                # 2026-07-29 批次化：子件同样建根批次
                child_root_batch = await self.create_root_batch(tpart)

                f_out = await part_files.upload(
                    owner_id=child_id,
                    kind=PartFileKind.DRAWING,
                    data=page_bytes,
                    original_filename=f"{asm_drawing_no}_p{page.page_index + 1}.pdf",
                    content_type="application/pdf",
                )
                # PR-H 2026-07-28：若该 page 挂 3D 模型则一并上传到 child_id
                await _maybe_upload_three_d_model(
                    owner_id=child_id,
                    item=page,
                    three_d_payloads=three_d_payloads_by_index,
                    part_files=part_files,
                )
                child_files.append(f_out)

                # 写 CREATED 事件
                await self.events.create(TPartEvent(
                    id=new_id(),
                    part_id=child_id,
                    batch_id=child_root_batch.id,
                    worker_id=None,
                    event_type=PartEventType.CREATED.value,
                    from_status=None,
                    to_status=PartStatus.PENDING.value,
                    drawing_code=None,
                    badge_code=None,
                    note=None,
                    quantity=page.quantity,
                    created_by=self._user_id,
                ))

                child_results.append(PartBatchTreePartResult(
                    uid=f"{asm_meta.uid}-{page.page_index}",
                    kind="assembly_child",
                    part=(await self._to_out([tpart]))[0],
                ))

            # master 文件（仅当用户选了 is_master）
            master_file = None
            if master_page is not None:
                master_bytes = all_pages[master_page.page_index]
                master_file = await part_files.upload(
                    owner_id=asm.id,
                    kind=PartFileKind.ASSEMBLY_MASTER,
                    data=master_bytes,
                    original_filename=f"{asm_drawing_no}_master.pdf",
                    content_type="application/pdf",
                )

            # 装配体输出
            assembly_out = AssemblyOut.model_construct(
                id=IdStrNonNull(str(asm.id)),
                version=asm.version,
                serial_no=asm.serial_no,
                drawing_no=asm.drawing_no,
                name=asm.name,
                applicant_name=asm.applicant_name,
                customer_id=IdStrNonNull(str(asm.customer_id)),
                customer_name=None,
                customer_path=None,
                parent_customer_name=None,
                request_date=asm.request_date,
                planned_delivery_date=asm.planned_delivery_date,
                actual_delivery_date=asm.actual_delivery_date,
                is_urgent=asm.is_urgent,
                status=asm.status,
                child_count=len(child_results),
                created_at=asm.created_at,
                updated_at=asm.updated_at,
            )
            assembly_results.append(PartBatchTreeAssemblyResult.model_construct(
                uid=asm_meta.uid,
                assembly=assembly_out,
                master_file=master_file,
                children=child_results,
                child_files=child_files,
            ))

            # 广播
            if self.event_broadcaster:
                try:
                    await self.event_broadcaster("ASSEMBLY_CREATED", {
                        "assembly_id": asm.id,
                        "drawing_no": asm.drawing_no,
                        "name": asm.name,
                        "child_count": len(child_results),
                    })
                except Exception:  # noqa: BLE001
                    pass

        # 2026-07-21：PartBatchTreeResult 含 "AssemblyOut" / "PartFileOut" 前向引用，
        # 用 model_construct() 直接构造（跳过 Pydantic 校验），因为 forward refs 在
        # schema.part 模块加载期尚未绑定到 AssemblyOut / PartFileOut。
        # 端到端校验由 FastAPI response_model 在序列化时统一执行。
        return PartBatchTreeResult.model_construct(
            standalone_parts=standalone_results,
            assemblies=assembly_results,
            failed=[],
        )

    async def update_part(
        self, part_id: int, data: PartUpdateRequest
    ) -> PartOut:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 2026-07-24：父装配体已设总价时，禁止子件再单独改价
        if part.assembly_id is not None and (
            data.unit_price is not None or data.total_price is not None
        ):
            if self.assemblies is not None:
                parent_asm = await self.assemblies.get_by_id(part.assembly_id)
                parent_total = getattr(parent_asm, "total_price", None) if parent_asm else None
                if parent_total is not None and (parent_total or Decimal("0")) > 0:
                    raise BizError(
                        code=ErrCode.BIZ_PART_PRICE_LOCKED_BY_ASSEMBLY,
                        message=(
                            f"零件 {part.id} 所属装配件 {part.assembly_id} 已设置总价 "
                            f"{parent_total}，子件不可单独改价"
                        ),
                        http_status=http_status.HTTP_400_BAD_REQUEST,
                    )

        if data.name is not None:
            part.name = data.name.strip()
        if data.drawing_no is not None:
            part.drawing_no = data.drawing_no.strip()
        if data.applicant_name is not None:
            part.applicant_name = data.applicant_name.strip()
        if data.quantity is not None:
            # 2026-07-29 批次化：总量变更只允许「唯一根批次且仍 PENDING」的工单
            # （尚未下发/领取/拆分），同步根批次数量保持 Σ批次=总量 不变量。
            if data.quantity != part.quantity:
                batches = await self._batches().list_by_part(part.id)
                if len(batches) != 1 or batches[0].status != "PENDING":
                    raise BizError(
                        code=ErrCode.BIZ_PART_QUANTITY_LOCKED,
                        message=(
                            "工单已下发或已拆分批次，禁止直接修改总量；"
                            "如需调整请取消后重建或拆分批次"
                        ),
                        http_status=http_status.HTTP_400_BAD_REQUEST,
                    )
                batches[0].quantity = data.quantity
                batches[0].updated_by = self._user_id
                await self._batches().update(batches[0])
            part.quantity = data.quantity
        if data.unit_price is not None:
            part.unit_price = data.unit_price
        if data.total_price is not None:
            part.total_price = data.total_price
        elif data.quantity is not None or data.unit_price is not None:
            # caller 没显式传总价 → 按最新单价/数量自动重算（防数据漂移）
            part.total_price = (part.unit_price or Decimal("0")) * (part.quantity or 0)
        if data.request_date is not None:
            part.request_date = data.request_date
        if data.planned_delivery_date is not None:
            part.planned_delivery_date = data.planned_delivery_date
        if data.actual_delivery_date is not None:
            part.actual_delivery_date = data.actual_delivery_date
        if data.is_urgent is not None:
            part.is_urgent = data.is_urgent
        if data.order_no is not None:
            part.order_no = data.order_no.strip() if data.order_no else None
        if data.system_delivery_date is not None:
            part.system_delivery_date = data.system_delivery_date
        if data.note is not None:
            part.note = data.note
        if data.customer_id is not None:
            new_cid = parse_snowflake_id(data.customer_id, field_name="customer_id")
            if new_cid is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {data.customer_id!r} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            cust = await self.customers.get_by_id(new_cid)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {data.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            part.customer_id = new_cid
        part.updated_by = self._user_id
        await self.parts.update(part)
        items = await self._to_out([part])
        return items[0]

    async def soft_delete_part(self, part_id: int) -> None:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.updated_by = self._user_id
        await self.parts.soft_delete(part)
        # 2026-07-29 批次化：级联软删全部批次
        for b in await self._batches().list_by_part(part.id):
            b.updated_by = self._user_id
            await self._batches().soft_delete(b)
        await self._broadcast()  # `deleted_at` 让该零件从 dashboard 快照消失

    # ============================================================
    # 新报工流程（货架 + 工人 + 品检）
    # ============================================================
    async def place_on_shelf(
        self, part_id: int, data: PlaceOnShelfRequest
    ) -> PartOut:
        """PENDING → IN_PROCESS：把零件放到生产货架。

        `shelf_id` 必须在 t_shelf 中存在 / is_active / zone=PRODUCTION。
        `next_process_id` 必填，service 校验 process 存在后喂给状态机。

        2026-07-29 批次化：可选 `batch_id`（默认唯一 PENDING 批次）+
        `quantity`（默认批次全量；部分量先拆再下发）。
        """
        part = await self._get_part_or_404(part_id)
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )
        batch = await self._resolve_target_batch(
            part, self._parse_batch_id(data),
            expect=lambda b: b.status == "PENDING",
            action="下发上架",
        )
        target = await self._maybe_split(part, batch, getattr(data, "quantity", None))

        # state machine handles status/location/holder mutation + event creation;
        # 状态机 on_enter_ON_SHELF 会同时把 next_process_id 设到 model 上。
        target.sm.place_on_shelf(
            shelf=shelf, process=process, event_repo=self.events,
            created_by=self._user_id,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "PLACED_ON_SHELF",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    def _parse_batch_id(self, data) -> int | None:
        """请求体里的 batch_id（雪花 ID 字符串）→ int；未携带 → None。"""
        raw = getattr(data, "batch_id", None)
        if raw is None or raw == "":
            return None
        parsed = parse_snowflake_id(raw, field_name="batch_id")
        if parsed is None:
            raise BizError(
                code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
                message=f"batch_id 不是合法的雪花 ID：{raw!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        return parsed

    async def _resolve_scan_batch(
        self,
        part: TPart,
        batch_id: int | None,
        *,
        location: str,
        holder_id: int,
        action: str,
    ) -> TPartBatch:
        """扫码路径的批次解析（领取/归还/送检）。

        缺省 batch_id 时的候选收窄顺序保持旧错误码语义：
        1. 状态/位置不符 → BIZ_INVALID_TRANSITION；
        2. 有候选但都不在 holder 手 → BIZ_AUTH_SHELF_MISMATCH（越权/拿错架）；
        3. holder 手有多个 → 400 要求指定 batch_id。
        """
        if batch_id is not None:
            return await self._resolve_target_batch(part, batch_id, action=action)
        batches = await self._batches().list_by_part(part.id)
        candidates = [
            b for b in batches
            if b.status == "IN_PROCESS" and b.location == location
        ]
        if not candidates:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=(
                    f"{action}：工单 {part.id} 没有 IN_PROCESS + {location} 的批次"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        mine = [b for b in candidates if b.current_holder_id == holder_id]
        if not mine:
            raise BizError(
                code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                message=(
                    f"{action}：批次不在 holder {holder_id} 手上"
                    f"（当前持有者 {[b.current_holder_id for b in candidates]}）"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if len(mine) > 1:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"{action}：该位置有 {len(mine)} 个批次"
                    f"（{', '.join(f'批次{b.batch_no}:{b.quantity}件' for b in mine)}），"
                    "请指定 batch_id"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        target = mine[0]
        target._part_serial = part.serial_no  # transient：SM 事件 drawing_code 用
        return target

    async def send_to_programming(
        self, part_id: int, *, batch_id: int | None = None,
        quantity: int | None = None,
    ) -> PartOut:
        """PENDING → PROGRAMMING：把零件发送至 CNC 编程。

        编程员在「待编程一览」看到这个零件，下载图纸/3D → 写程序 →
        上传 G 代码 → 在编程员端调用 `release_from_programming` 下发到货架。

        2026-07-29 批次化：可选 batch_id / quantity（部分量先拆再送）。
        """
        part = await self._get_part_or_404(part_id)
        batch = await self._resolve_target_batch(
            part, batch_id,
            expect=lambda b: b.status == "PENDING",
            action="送编程",
        )
        target = await self._maybe_split(part, batch, quantity)
        target.sm.send_to_programming(
            event_repo=self.events, created_by=self._user_id,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "SENT_TO_PROGRAMMING",
            self._banner_payload(
                part, customer_path=items[0].customer_path,
            ),
        )
        return items[0]

    async def send_to_outsource(
        self, part_id: int, data: SendToOutsourceRequest,
    ) -> PartOut:
        """IN_PROCESS + PRODUCTION_SHELF → OUTSOURCE：把零件从外协工序货架发送给外协公司。

        2026-07-28 PR-H 重构：发送外协统一从「绑定了 OUTSOURCE 工序的货架」上发出，
        PENDING 状态零件必须先上架再走外协。

        校验：
        - outsource_company_id 存在 + 未软删 + is_active=True
        - next_process_id 存在 + category=OUTSOURCE
        - 公司映射了该 OUTSOURCE 工序（t_outsource_company_process）
        - part 位于绑定了 OUTSOURCE 工序的货架（status=IN_PROCESS + location=PRODUCTION_SHELF + current_holder_id ∈ OUTSOURCE-bound shelves）
        - data.version 与目标批次 batch.version 一致（OCC；2026-07-29 批次化后改为批次 version）

        行为分支（由 next_process.requires_approval 决定）：
        - True（默认）：必须有该 (part, company, process) 元组的 APPROVED 报价；
          发送后把报价 mark_used。
        - False（无需审批，2026-07-28 新增）：跳过报价检查；事件 note 追加「直接发送（无需审批）」。
        """
        if self.outsource_companies is None or self.outsource_company_process is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing outsource repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        part = await self._get_part_or_404(part_id)

        # 1. parse_snowflake_id(company_id) → int
        company_id_int = parse_snowflake_id(
            data.outsource_company_id, field_name="outsource_company_id",
        )
        if company_id_int is None:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_NOT_FOUND,
                message=(
                    f"outsource company {data.outsource_company_id!r} not found"
                ),
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 2. 公司存在 + 未软删 + 启用
        company = await self.outsource_companies.get_by_id(company_id_int)
        if company is None or not company.is_active:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_NOT_FOUND,
                message=(
                    f"outsource company {data.outsource_company_id} not found"
                ),
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 3. 工序存在 + OUTSOURCE 类别
        process_id_int = parse_snowflake_id(
            data.next_process_id, field_name="next_process_id",
        )
        if process_id_int is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"next_process_id 不是合法的雪花 ID 字符串："
                    f"{data.next_process_id!r}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        process = await self._get_process(process_id_int)
        if process.category != ProcessCategory.OUTSOURCE.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS,
                message=(
                    f"工序「{process.code}」不是 OUTSOURCE 类别，"
                    "无法用于发送外协"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        # 4. 公司映射了该工序
        mapped = await self.outsource_company_process.list_process_ids_by_outsource_company(
            company_id_int, include_deleted=False,
        )
        if process.id not in mapped:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_PROCESS_NOT_MAPPED,
                message=(
                    f"外协公司「{company.name}」未映射工序「{process.code}」，"
                    "请先在外协管理中维护工序能力清单"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 5. [2026-07-28 PR-H] 状态资格防御闸：必须位于绑定了 OUTSOURCE 工序的货架
        # 2026-07-29 批次化：批次解析放在公司/工序校验之后（保持错误码优先级），
        # 不设 expect —— 不合格状态统一由下面的闸门抛 422（与旧错误码一致）。
        batch = await self._resolve_target_batch(
            part, self._parse_batch_id(data),
            action="发送外协",
        )
        # 0. OCC 校验（2026-07-29 批次化修正）：外协可发送列表回传的是批次 version，
        # 显式校验在批次解析后立即拦截并发冲突；真正兜底仍是 AuditMixin 的 WHERE version=?。
        if batch.version != data.version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message="该批次已被其他用户修改，请刷新后重试",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if self.shelf_process_repo is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing shelf_process repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        outsource_shelf_ids = await self.shelf_process_repo.list_shelf_ids_with_process_category(
            ProcessCategory.OUTSOURCE.value,
        )
        if not outsource_shelf_ids:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_NO_SHELF,
                message=(
                    "系统无任何绑定了外协工序的货架，请先在 /shelves/{id}/processes 配置"
                ),
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if (
            batch.status != PartStatus.IN_PROCESS.value
            or batch.location != PartLocation.PRODUCTION_SHELF.value
            or batch.current_holder_id not in outsource_shelf_ids
        ):
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF,
                message=(
                    f"发送外协必须从「绑定了外协工序的货架」上发出（当前 C2 等 OUTSOURCE 货架）；"
                    f"批次 {batch.batch_no} 当前 status={batch.status} location={batch.location} "
                    f"current_holder_id={batch.current_holder_id}"
                ),
                http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # 6. 2026-07-30：报价处理（回归纯审批对象）
        if self.outsource_quotes is None or self.outsource_shipments is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing outsource repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if process.requires_approval:
            # 6a. APPROVAL：按 (part_id, process_id) 查 is_direct=false 的 APPROVED 报价
            target_quote = await self.outsource_quotes.get_approved_for_part_process(
                part_id=part.id,
                process_id=process.id,
                is_direct=False,
            )
            if target_quote is None:
                raise BizError(
                    code=ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_APPROVED,
                    message=(
                        f"未找到工序「{process.code}」的已批准报价，"
                        "请先在「报价一览」中提交并由 MANAGER 审核通过"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            # 报价公司必须与请求公司一致（公司由报价锁定）
            if target_quote.outsource_company_id != company_id_int:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"报价锁定外协公司与此处不一致："
                        f"报价公司={target_quote.outsource_company_id}，"
                        f"请求公司={company_id_int}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
        else:
            # 6b. DIRECT：按 (part_id, process_id) 有任一 APPROVED 报价（含占位）则用之
            target_quote = await self.outsource_quotes.get_approved_for_part_process(
                part_id=part.id,
                process_id=process.id,
                is_direct=None,
            )
            if target_quote is None:
                from model.outsource_quote import TOutsourceQuote
                from model.outsource_quote_event import TOutsourceQuoteEvent
                from model.enums import OutsourceQuoteEventType
                new_quote = TOutsourceQuote(
                    id=new_id(),
                    part_id=part.id,
                    outsource_company_id=company_id_int,
                    process_id=process.id,
                    price=Decimal("0"),
                    status=OutsourceQuoteStatus.APPROVED.value,
                    is_direct=True,
                    review_note="系统自动创建（DIRECT 直接发送）",
                    reviewed_at=now_naive(),
                    created_by=self._user_id,
                    updated_by=self._user_id,
                )
                await self.outsource_quotes.create(new_quote)
                await self.quote_events.create(TOutsourceQuoteEvent(
                    id=new_id(),
                    quote_id=new_quote.id,
                    event_type=OutsourceQuoteEventType.CREATED.value,
                    from_status=None,
                    to_status=OutsourceQuoteStatus.APPROVED.value,
                    note="DIRECT 直接发送：系统自动创建并直接置为 APPROVED",
                    created_by=self._user_id,
                ))
                target_quote = new_quote

        # 7. 批次状态机：IN_PROCESS/PRODUCTION_SHELF → OUTSOURCE
        # 2026-07-29 批次化：部分量先拆再发，状态机作用在拆出的子批上
        target = await self._maybe_split(
            part, batch, getattr(data, "quantity", None),
        )
        target.sm.send_to_outsource(
            outsource_company=company, process=process,
            event_repo=self.events, created_by=self._user_id,
            direct_send=not process.requires_approval,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)

        # 8. 创建 shipment 记录（2026-07-30：报价不再改状态）
        from model.outsource_shipment import TOutsourceShipment
        shipment = TOutsourceShipment(
            id=new_id(),
            quote_id=target_quote.id,
            part_id=part.id,
            batch_id=target.id,
            outsource_company_id=target_quote.outsource_company_id,
            process_id=process.id,
            quantity=target.quantity,
            unit_price=target_quote.price,
            status="OUTSOURCING",
            sent_at=now_naive(),
            created_by=self._user_id,
            updated_by=self._user_id,
        )
        await self.outsource_shipments.create(shipment)

        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "SENT_TO_OUTSOURCE",
            self._banner_payload(
                part, customer_path=items[0].customer_path,
                shelf_code=None,
            ),
        )
        return items[0]

    async def receive_from_outsource(
        self, part_id: int, data: PlaceOnShelfRequest,
    ) -> PartOut:
        """OUTSOURCE → IN_PROCESS：从外协回收，下发到生产货架继续加工。

        body 复用 PlaceOnShelfRequest（shelf_id + next_process_id + 可选 outsource_company_id）；
        额外校验：next_process_id 必须是 INHOUSE（外协回来后通常进车间）。

        2026-07-28：`outsource_company_id` 入参（可空）写入 TPartEvent.outsource_company_id
        用于外协对账；前端从 part_event 历史查最近 SENT_TO_OUTSOURCE 的公司 id 填入。

        PR-H 2026-07-29：反查 (part, company, process) 的 OUTSOURCING 报价 →
        mark_received + 写 received_at（外协统一事实表生命周期）。
        """
        part = await self._get_part_or_404(part_id)
        batch = await self._resolve_target_batch(
            part, self._parse_batch_id(data),
            expect=lambda b: b.status == PartStatus.OUTSOURCE.value,
            action="外协回收",
        )
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )
        if process.category != ProcessCategory.INHOUSE.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS,
                message=(
                    f"工序「{process.code}」不是 INHOUSE 类别，"
                    "外协回收后必须回到车间自产工序"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 解析 outsource_company_id（对账审计字段，可空）
        outsource_company_id_int: int | None = None
        if data.outsource_company_id:
            parsed = parse_snowflake_id(
                data.outsource_company_id, field_name="outsource_company_id",
            )
            if parsed is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"outsource_company_id 不是合法的雪花 ID 字符串："
                        f"{data.outsource_company_id!r}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            outsource_company_id_int = parsed

        # 状态机转换：落到 ON_SHELF（on_enter_ON_SHELF 设置 shelf/process/holder/placed_at）
        target = await self._maybe_split(part, batch, getattr(data, "quantity", None))
        target.sm.receive_from_outsource(
            shelf=shelf, process=process,
            event_repo=self.events, created_by=self._user_id,
            outsource_company_id=outsource_company_id_int,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)

        # 2026-07-30：关闭/拆分 shipment
        current_outsource_proc_id = (
            int(part.next_process_id) if part.next_process_id else None
        )
        await self._mark_shipment_received(
            part_id=part.id,
            source_batch=batch,
            target_batch=target,
            company_id=outsource_company_id_int,
            process_id=current_outsource_proc_id,
        )

        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "RECEIVED_FROM_OUTSOURCE",
            self._banner_payload(
                part, customer_path=items[0].customer_path,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    async def receive_from_outsource_to_inspection(
        self, part_id: int, data: ReceiveToInspectionRequest,
    ) -> PartOut:
        """2026-07-16：OUTSOURCE → INSPECTION：外协件直接送检（跳过生产货架）。

        body:
        - shelf_id（必须 INSPECTION 区 active）
        - auto_pass_inspection：True 时一次性把状态推到 READY_TO_SHIP
          （相当于「外协 → 品检 → 通过品检 → 待送货」两步压缩为一次操作；
          用于信任外协质量的快捷流程）。
        - outsource_company_id（2026-07-28 可选）：写入 TPartEvent.outsource_company_id 对账。

        复用现有 pass_inspection 实现二次转换（同一事务连续两次状态机调用）。

        PR-H 2026-07-29：同 receive_from_outsource，反查 OUTSOURCING 报价 → mark_received。
        """
        part = await self._get_part_or_404(part_id)
        batch = await self._resolve_target_batch(
            part, self._parse_batch_id(data),
            expect=lambda b: b.status == PartStatus.OUTSOURCE.value,
            action="外协回收送检",
        )
        target_shelf = await self._validate_inspection_shelf(data.shelf_id)

        # 解析 outsource_company_id（对账审计字段，可空）
        outsource_company_id_int: int | None = None
        if data.outsource_company_id:
            parsed = parse_snowflake_id(
                data.outsource_company_id, field_name="outsource_company_id",
            )
            if parsed is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"outsource_company_id 不是合法的雪花 ID 字符串："
                        f"{data.outsource_company_id!r}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            outsource_company_id_int = parsed

        # 第一次转换：OUTSOURCE → INSPECTION
        target = await self._maybe_split(part, batch, getattr(data, "quantity", None))
        target.sm.inspect_from_outsource(
            target_shelf=target_shelf,
            event_repo=self.events, created_by=self._user_id,
            outsource_company_id=outsource_company_id_int,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)

        # 2026-07-30：关闭/拆分 shipment
        current_next_proc_id_int = (
            int(part.next_process_id) if part.next_process_id else None
        )
        await self._mark_shipment_received(
            part_id=part.id,
            source_batch=batch,
            target_batch=target,
            company_id=outsource_company_id_int,
            process_id=current_next_proc_id_int,
        )

        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "RECEIVED_FROM_OUTSOURCE_INSPECTED",
            self._banner_payload(
                part, customer_path=items[0].customer_path,
                shelf_code=target_shelf.code,
            ),
        )

        # auto_pass_inspection=True：再触发 INSPECTION → READY_TO_SHIP（品检通过）
        if data.auto_pass_inspection:
            return await self.pass_inspection(part.id, batch_id=target.id)
        return items[0]

    async def _validate_inspection_shelf(self, shelf_id: str) -> TShelf:
        """校验品检货架存在 + 启用 + zone=INSPECTION。"""
        if not shelf_id:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="shelf_id 必填",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        shelf_id_int = parse_snowflake_id(shelf_id, field_name="shelf_id")
        if shelf_id_int is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"shelf_id 不是合法的雪花 ID：{shelf_id!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        shelf = await self.shelves.get_by_id(shelf_id_int)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not shelf.is_active:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf「{shelf.code}」已停用",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if shelf.zone != ShelfZone.INSPECTION.value:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NO_MATCH_FOR_PROCESS,
                message=(
                    f"货架「{shelf.code}」不是品检区(INSPECTION)，"
                    "外协回收送检请选用 INSPECTION 货架"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        return shelf

    async def release_from_programming(
        self, part_id: int, data: PlaceOnShelfRequest
    ) -> PartOut:
        """PROGRAMMING → IN_PROCESS：编程员把零件下发到生产货架。

        与 place_on_shelf 走同一个货架/工序校验，落到 ON_SHELF 状态机入口
        复用同一份 on_enter_ON_SHELF 副作用；事件类型为 CNC_RELEASED
        （见 on_release_from_programming 回调）。
        """
        part = await self._get_part_or_404(part_id)
        # 货架 / 工序校验
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )
        # 批次解析 + 状态机转换（2026-07-29 批次化）
        batch = await self._resolve_target_batch(
            part, self._parse_batch_id(data),
            expect=lambda b: b.status == "PROGRAMMING",
            action="编程下发",
        )
        target = await self._maybe_split(part, batch, getattr(data, "quantity", None))
        target.sm.release_from_programming(
            shelf=shelf, process=process, event_repo=self.events,
            created_by=self._user_id,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "CNC_RELEASED",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    async def _get_part_or_404(self, part_id: int) -> TPart:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return part

    async def _validate_production_shelf_and_process(
        self, shelf_id: int | None, next_process_id: int | None,
    ) -> tuple[TShelf, TProcess]:
        """校验 `shelf_id` 是 PRODUCTION 区 active 货架 + `next_process_id` 存在
        + **该货架已映射该工序**（2026-07-17 强化）。

        三道闸：(1) shelf 存在/active/PRODUCTION；(2) process 存在；(3) shelf↔process
        在 `t_shelf_process` 中存在活跃行。三者全过才返回。三个调用方
        （`place_on_shelf` / `release_from_programming` / `receive_from_outsource`）
        都通过本 helper 一次校验。
        """
        if shelf_id is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="shelf_id is required",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        shelf = await self.shelves.get_by_id(shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not shelf.is_active:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf {shelf.code!r} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if shelf.zone != ShelfZone.PRODUCTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"shelf {shelf.code!r} is zone={shelf.zone!r}; "
                    "this operation requires PRODUCTION"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if next_process_id is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="next_process_id is required",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        process = await self._get_process(next_process_id)
        # 2026-07-17：货架↔工序 映射校验（兜底）
        await self._assert_shelf_maps_process(shelf, process)
        return shelf, process

    async def _assert_shelf_maps_process(
        self, shelf: TShelf, process: TProcess,
    ) -> None:
        """校验 `shelf` 已映射 `process`；不满足抛 `BIZ_SHELF_PROCESS_NOT_MAPPED` 422。

        - `shelf_process_repo is None` → 500 配置错误（service 没接 repo，调用方不能静默放过）
        - 货架没有任何映射行（空集）→ 同样拒绝（用户必须先在「货架管理」配置映射）
        - 货架有映射但不包含此 process → 422 拒绝

        单独抽出便于：`place_on_shelf` / `release_from_programming` /
        `receive_from_outsource` 通过 `_validate_production_shelf_and_process` 走；
        `complete_repair`（货架来自 query 参数，且 next_process 由工种→默认工序
        推导）直接调本方法。
        """
        if self.shelf_process_repo is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="shelf_process_repo not configured for mapping guard",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        allowed_ids = await self.shelf_process_repo.list_process_ids_by_shelf(shelf.id)
        if not allowed_ids or process.id not in allowed_ids:
            raise BizError(
                code=ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED,
                message=(
                    f"货架 {shelf.code!r} 未配置可执行工序 {process.code!r}，"
                    f"请先在「货架管理」→「工序映射」中配置"
                ),
                http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

    async def _mark_shipment_received(
        self,
        *,
        part_id: int,
        source_batch: TPartBatch,
        target_batch: TPartBatch,
        company_id: int | None,
        process_id: int | None,
    ) -> None:
        """2026-07-30：外协接收时关闭/拆分 shipment。

        - 优先按 source_batch_id 查 OUTSOURCING shipment。
        - 查不到再兜底 (part, company, process) 最新一条 OUTSOURCING。
        - 全量收（target == source）：shipment → RECEIVED。
        - 部分收（target != source 或 quantity 不同）：源 shipment 减量保持 OUTSOURCING
          （剩 0 则 RECEIVED），另插新 shipment（batch_id=target.id, status=RECEIVED）。
        - 找不到时静默跳过（兼容历史流程）。
        """
        if self.outsource_shipments is None:
            return

        # 兜底：company_id / process_id 缺省时取 part 最近的 SENT 事件
        if company_id is None or process_id is None:
            latest_sent = await self.events.latest_sent_to_outsource_for_part(part_id)
            if latest_sent is None:
                return
            if company_id is None and latest_sent.outsource_company_id is not None:
                company_id = int(latest_sent.outsource_company_id)
            if process_id is None and getattr(latest_sent, "process_id", None) is not None:
                process_id = int(latest_sent.process_id)
        if company_id is None or process_id is None:
            return

        shipment = await self.outsource_shipments.get_open_by_batch_id(source_batch.id)
        if shipment is None:
            shipment = await self.outsource_shipments.find_open_by_part_company_process(
                part_id=part_id,
                company_id=company_id,
                process_id=process_id,
            )
        if shipment is None:
            return

        received_qty = target_batch.quantity
        if target_batch.id == source_batch.id:
            # 全量收
            shipment.status = "RECEIVED"
            shipment.received_at = now_naive()
            shipment.updated_by = self._user_id
            await self.outsource_shipments.update(shipment)
        else:
            # 部分收：镜像拆分
            shipment.quantity -= received_qty
            if shipment.quantity <= 0:
                shipment.status = "RECEIVED"
                shipment.received_at = now_naive()
            shipment.updated_by = self._user_id
            await self.outsource_shipments.update(shipment)

            from model.outsource_shipment import TOutsourceShipment
            new_shipment = TOutsourceShipment(
                id=new_id(),
                quote_id=shipment.quote_id,
                part_id=shipment.part_id,
                batch_id=target_batch.id,
                outsource_company_id=shipment.outsource_company_id,
                process_id=shipment.process_id,
                quantity=received_qty,
                unit_price=shipment.unit_price,
                status="RECEIVED",
                sent_at=shipment.sent_at,
                received_at=now_naive(),
                created_by=self._user_id,
                updated_by=self._user_id,
            )
            await self.outsource_shipments.create(new_shipment)

    async def _get_process(self, process_id: int) -> TProcess:
        """取工序对象；不存在抛 BIZ_PROCESS_NOT_FOUND。"""
        if self.processes is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="processes repo not configured",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        p = await self.processes.get_by_id(process_id)
        if p is None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {process_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return p

    async def pick_up_by_scan(self, data: PartPickUpRequest) -> PartOut:
        """工人扫 serial_no 领取：状态仍是 IN_PROCESS；holder 由 shelf → worker。

        不再是状态变更；只是 `current_holder_id` 在「工人↔货架」之间切换。
        加 `with_for_update()` 防两人并发领同一 serial。
        """
        # 货架和工人先取出
        shelf = await self.shelves.get_by_id(data.shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {data.shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if shelf.zone != ShelfZone.PRODUCTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"shelf {shelf.code!r} is zone={shelf.zone!r}; "
                    "pick-up only valid at PRODUCTION shelf"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        worker = await self.workers.get_by_badge_code(data.badge_code)
        if worker is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker with badge_code {data.badge_code!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not worker.is_active:
            raise BizError(
                code=ErrCode.BIZ_WORKER_INACTIVE,
                message=f"worker {worker.name} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        part = await self.parts.get_by_serial(data.serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part with serial_no {data.serial_no!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 2026-07-29 批次化：定位目标批次（默认该货架上唯一 ON_SHELF 批次；
        # 前端卡片列表已带 batch_id）。部分量 → 先拆再领。
        batch = await self._resolve_scan_batch(
            part, self._parse_batch_id(data),
            location="PRODUCTION_SHELF",
            holder_id=shelf.id,
            action="领取",
        )
        if batch.status != "IN_PROCESS" or batch.location != "PRODUCTION_SHELF":
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=(
                    f"pick-up requires IN_PROCESS on PRODUCTION_SHELF; "
                    f"batch {batch.batch_no} status={batch.status}, location={batch.location}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if batch.current_holder_id != shelf.id:
            raise BizError(
                code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                message=(
                    f"batch is currently held by {batch.current_holder_id}; "
                    f"expected this shelf {shelf.id}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        target = await self._maybe_split(
            part, batch, getattr(data, "quantity", None),
        )
        # state machine handles holder switch + event creation
        target.sm.pick_up(
            worker=worker, shelf=shelf, event_repo=self.events,
            created_by=self._user_id,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        await self._broadcast_event(
            "PICKED_UP",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                worker_name=worker.name,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    async def scan_event(self, data: PartScanRequest) -> PartOut:
        """RETURNED：把当前由工人持有的零件放回生产货架（holder = shelf_id）；
        状态不变（仍是 IN_PROCESS）。

        INSPECTED：从工人手送检到品检货架；状态 → INSPECTION；holder → inspection shelf。
        """
        shelf = await self.shelves.get_by_id(data.shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {data.shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        worker = await self.workers.get_by_badge_code(data.badge_code)
        if worker is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker with badge_code {data.badge_code!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not worker.is_active:
            raise BizError(
                code=ErrCode.BIZ_WORKER_INACTIVE,
                message=f"worker {worker.name} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        part = await self.parts.get_by_serial(data.serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part with serial_no {data.serial_no!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        event_type = _parse_event_type(data.event_type)

        # 2026-07-29 批次化：定位工人持有的批次（默认唯一；部分量先拆再转）。
        batch = await self._resolve_scan_batch(
            part, self._parse_batch_id(data),
            location="WORKER",
            holder_id=worker.id,
            action="归还/送检",
        )

        if event_type == PartEventType.RETURNED:
            if batch.status != "IN_PROCESS" or batch.location != "WORKER":
                raise BizError(
                    code=ErrCode.BIZ_INVALID_TRANSITION,
                    message=(
                        f"return requires IN_PROCESS with WORKER location; "
                        f"batch {batch.batch_no} status={batch.status}, location={batch.location}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if shelf.zone != ShelfZone.PRODUCTION.value:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"shelf {shelf.code!r} is zone={shelf.zone!r}; "
                        "return only valid at PRODUCTION shelf"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if batch.current_holder_id != worker.id:
                raise BizError(
                    code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                    message=(
                        f"batch is not held by worker {worker.id}; "
                        "only the current holder can return it"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            # RETURNED 必填 next_process_id
            if data.next_process_id is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="RETURNED requires next_process_id",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            new_process = await self._get_process(data.next_process_id)
            # 2026-07-17：货架↔工序 映射校验收紧（去掉之前空集时跳过的 permissive 行为）
            await self._assert_shelf_maps_process(shelf, new_process)
            # 解析 prev_process_code 与 worker_work_type_code 给状态机 note 用
            prev_process_code: str | None = None
            if self.processes is not None and batch.next_process_id is not None:
                prev = await self.processes.get_by_id(batch.next_process_id)
                if prev is not None:
                    prev_process_code = prev.code
            worker_work_type_code: str | None = None
            if (
                self.work_types is not None
                and worker.work_type_id is not None
            ):
                wt = await self.work_types.get_by_id(worker.work_type_id)
                if wt is not None:
                    worker_work_type_code = wt.code

            target = await self._maybe_split(
                part, batch, getattr(data, "quantity", None),
            )
            target.sm.return_to_shelf(
                worker=worker,
                shelf=shelf,
                process=new_process,
                prev_process_code=prev_process_code,
                worker_work_type_code=worker_work_type_code,
                event_repo=self.events,
                created_by=self._user_id,
            )
            target.updated_by = self._user_id
            await self._batches().update(target)
            await self._after_batch_transition(part)
            items = await self._to_out([part])
            await self._broadcast_event(
                "RETURNED",
                self._banner_payload(
                    part,
                    customer_path=items[0].customer_path,
                    worker_name=worker.name,
                    shelf_code=shelf.code,
                ),
            )
            return items[0]

        if event_type == PartEventType.INSPECTED:
            if data.target_inspection_shelf_id is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="INSPECTED requires target_inspection_shelf_id",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            target_shelf = await self.shelves.get_by_id(data.target_inspection_shelf_id)
            if target_shelf is None or target_shelf.deleted_at is not None:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_NOT_FOUND,
                    message=f"inspection shelf {data.target_inspection_shelf_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if target_shelf.zone != ShelfZone.INSPECTION.value:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"target shelf {target_shelf.code!r} is zone={target_shelf.zone!r}; "
                        "INSPECTED requires INSPECTION zone"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if not target_shelf.is_active:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_IN_USE,
                    message=f"inspection shelf {target_shelf.code!r} is inactive",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if batch.current_holder_id != worker.id:
                raise BizError(
                    code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                    message="batch is not held by current worker",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

            target = await self._maybe_split(
                part, batch, getattr(data, "quantity", None),
            )
            target.sm.inspect(
                worker=worker, target_shelf=target_shelf, event_repo=self.events,
                created_by=self._user_id,
            )
            target.updated_by = self._user_id
            await self._batches().update(target)
            await self._after_batch_transition(part)
            items = await self._to_out([part])
            await self._broadcast_event(
                "INSPECTED",
                self._banner_payload(
                    part,
                    customer_path=items[0].customer_path,
                    worker_name=worker.name,
                    shelf_code=target_shelf.code,
                ),
            )
            return items[0]

        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=(
                f"scan endpoint only accepts RETURNED or INSPECTED, got {event_type.value!r}"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )

    # ============================================================
    # 扫码台 PICK_UP 列表（按工种 + 货架过滤）
    # ============================================================
    async def list_pickable_parts(
        self, work_type_id: int, shelf_id: int
    ) -> list[PartOut]:
        """列出当前生产货架上、由指定工种可领的零件。

        短路：worker.work_type_id 未传 → []；
              工种未映射任何工序 → []；
              货架不存在 → []。

        返回 [] 时 UI 提示「无可领件 / 工种映射为空 / 请联系管理员」。
        """
        if work_type_id is None:
            return []
        if self.work_type_process is None:
            return []
        process_ids = await self.work_type_process.list_process_ids_by_work_type(
            work_type_id, include_deleted=False,
        )
        if not process_ids:
            return []
        rows = await self._batches().list_for_work_type(
            shelf_id=shelf_id,
            mapped_process_ids=process_ids,
        )
        return await self._to_batch_out(rows)

    async def list_pickable_parts_all_shelves(
        self,
        work_type_id: int,
        *,
        shelf_ids: list[int] | None = None,
    ) -> list[PartOut]:
        """共享 HMI PICK_UP 跨架列表：列出 HMI 货架范围内、由指定工种
        可领的零件。前端按 `current_holder_id` 在卡片网格里分组。

        与 `list_pickable_parts` 差异：去掉单架 shelf_id 过滤，改成可选
        `shelf_ids` 多架过滤（None = 全架；空 list = 永远空——给"非 HMI
        角色"返回空）。保留工种过滤 + 短路逻辑一致。

        `next_process_id` 条件由 repository 层处理：IN mapped_process_ids
        OR IS NULL（未指定下一道工序）。
        """
        if work_type_id is None:
            return []
        if self.work_type_process is None:
            return []
        process_ids = await self.work_type_process.list_process_ids_by_work_type(
            work_type_id, include_deleted=False,
        )
        if not process_ids:
            return []
        if shelf_ids is not None and not shelf_ids:
            return []  # 非 HMI 角色 → HMI scope 为空，短路免 DB
        rows = await self._batches().list_for_work_type_all_shelves(
            mapped_process_ids=process_ids,
            shelf_ids=shelf_ids,
        )
        return await self._to_batch_out(rows)

    async def list_parts_held_by_worker(
        self,
        worker_id: int,
    ) -> list[PartOut]:
        """扫码台 RETURN 新流程：列出当前由某工人持有的所有零件。

        短路：worker_id 为 None / 0 → []（避免空指针 DB 查询）。

        返回 [] 时前端提示「您当前没有持有零件 / 请先领取」。
        service 不校验 worker 存在性（trust 客户端已在 useScanSession 拿到 worker.id）；
        若 worker_id 不存在就返回空 list，等价于「他没持有任何零件」。
        """
        if not worker_id:
            return []
        rows = await self._batches().list_held_by_worker(worker_id=worker_id)
        return await self._to_batch_out(rows)

    async def list_inspection_batches(
        self,
        *,
        keyword: str | None = None,
        customer_id: str | None = None,
        serial_no: str | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[PartOut], int]:
        """品检待办（2026-07-29 批次级）：INSPECTION 批次 + 工单展示字段。

        行=批次：同一工单多个品检批次各占一行，pass/fail 操作回传 batch_id。
        返回 (items, total)。
        """
        customer_ids_in: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is not None:
                customer_ids_in = await expand_customer_ids(self.customers, cid_int)
        kw = (keyword or "").strip() or None
        rows = await self._batches().list_batches_with_part(
            statuses=[PartStatus.INSPECTION.value],
            customer_ids_in=customer_ids_in,
            keyword=kw,
            serial_no=(serial_no or "").strip() or None,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            limit=limit,
            offset=offset,
        )
        total = await self._batches().count_batches_with_part(
            statuses=[PartStatus.INSPECTION.value],
            customer_ids_in=customer_ids_in,
            keyword=kw,
            serial_no=(serial_no or "").strip() or None,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
        )
        return await self._to_batch_out(rows), total

    async def list_repair_batches(
        self,
        *,
        keyword: str | None = None,
        customer_id: str | None = None,
        serial_no: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[PartOut], int]:
        """PR-M 2026-08-04 「返修接收」Tab 1 (已送货): 列出 DELIVERED 批次.

        复用 list_inspection_batches 的 query/sort 骨架; 行=批次, 每个批次
        在前端对应一个「开始返修」按钮.
        """
        customer_ids_in: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is not None:
                customer_ids_in = await expand_customer_ids(self.customers, cid_int)
        kw = (keyword or "").strip() or None
        rows = await self._batches().list_batches_with_part(
            statuses=[PartStatus.DELIVERED.value],
            customer_ids_in=customer_ids_in,
            keyword=kw,
            serial_no=(serial_no or "").strip() or None,
            limit=limit,
            offset=offset,
        )
        total = await self._batches().count_batches_with_part(
            statuses=[PartStatus.DELIVERED.value],
            customer_ids_in=customer_ids_in,
            keyword=kw,
            serial_no=(serial_no or "").strip() or None,
        )
        return await self._to_batch_out(rows), total

    async def list_repairing_batches(
        self,
        *,
        keyword: str | None = None,
        customer_id: str | None = None,
        serial_no: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[PartOut], int]:
        """PR-M 2026-08-04 「返修接收」Tab 2 (返修中): 列出 REPAIRING 批次.

        行=批次; 每个批次在前端对应一个「完成返修」按钮 (REPAIRING -> ON_SHELF/INSPECTION).
        """
        customer_ids_in: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is not None:
                customer_ids_in = await expand_customer_ids(self.customers, cid_int)
        kw = (keyword or "").strip() or None
        rows = await self._batches().list_batches_with_part(
            statuses=[PartStatus.REPAIRING.value],
            customer_ids_in=customer_ids_in,
            keyword=kw,
            serial_no=(serial_no or "").strip() or None,
            limit=limit,
            offset=offset,
        )
        total = await self._batches().count_batches_with_part(
            statuses=[PartStatus.REPAIRING.value],
            customer_ids_in=customer_ids_in,
            keyword=kw,
            serial_no=(serial_no or "").strip() or None,
        )
        return await self._to_batch_out(rows), total

    # ============================================================
    # 统一外协可发送一览（2026-07-28 新增；取代 list_direct_outsource_candidates）
    # ============================================================
    async def list_outsource_sendable(
        self,
        *,
        keyword: str | None = None,
        customer_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> OutsourceSendableListOut:
        """外协可发送一览（统一查询）：合并 APPROVAL（有报价）和 DIRECT（无需审批可直发）两路。

        2026-07-29 PR-fix-0.2.0 批次化：行=批次（之前行=工单，因 rollup 派生字段而漏显可发批次）。
        谓词：
        - APPROVAL：TPartBatch.part_id ∈ part_ids_with_approved_quote ∩ TPartBatch.next_process_id ∈ approval_proc_ids
          ∩ (TPartBatch.status=PENDING OR TPartBatch.status=IN_PROCESS+location=PRODUCTION_SHELF)
        - DIRECT：TPartBatch.next_process_id ∈ direct_proc_ids
          ∩ (TPartBatch.status=PENDING OR TPartBatch.status=IN_PROCESS+location=PRODUCTION_SHELF)

        C2 货架**不**在此过滤；send_to_outsource 服务层在中间外协（IN_PROCESS）路径
        做 C2 前置校验。起始外协（PENDING）直发不要求 C2。
        """
        if (
            self.processes is None
            or self.outsource_company_process is None
            or self.outsource_companies is None
            or self.outsource_quotes is None
        ):
            return OutsourceSendableListOut(
                items=[], total=0, limit=limit, offset=offset,
            )

        # 1. 拉全部 OUTSOURCE 工序，按 requires_approval 分两组
        processes = await self.processes.list_with_filters(
            category="OUTSOURCE", include_deleted=False, limit=500, offset=0,
        )
        direct_proc_ids = {p.id for p in processes if not p.requires_approval}
        approval_proc_ids = {p.id for p in processes if p.requires_approval}
        process_map = {p.id: p for p in processes}

        if not direct_proc_ids and not approval_proc_ids:
            return OutsourceSendableListOut(
                items=[], total=0, limit=limit, offset=offset,
            )

        # 2. 直发：批查 company mappings，过滤掉失效 company
        direct_proc_to_companies: dict[int, list[int]] = {}
        all_direct_company_ids: set[int] = set()
        if direct_proc_ids:
            mappings = await self.outsource_company_process.list_companys_by_processes(
                list(direct_proc_ids), include_deleted=False,
            )
            for m in mappings:
                direct_proc_to_companies.setdefault(m.process_id, []).append(
                    m.outsource_company_id,
                )
                all_direct_company_ids.add(m.outsource_company_id)
        # 取 active companies
        active_company_map: dict[int, object] = {}
        if all_direct_company_ids:
            companies = await self.outsource_companies.list_by_ids(list(all_direct_company_ids))
            active_company_map = {c.id: c for c in companies if c.is_active}
        # 过滤掉失效 company，整理为 proc_id -> sorted[company_id]
        direct_proc_to_active_companies: dict[int, list[int]] = {}
        for proc_id, cids in direct_proc_to_companies.items():
            valid = [cid for cid in cids if cid in active_company_map]
            if valid:
                direct_proc_to_active_companies[proc_id] = sorted(valid)

        # 3. 审批：批查所有 APPROVED 报价，按 (part_id) 取每 part 最新一条
        approved_by_part: dict[int, object] = {}
        if approval_proc_ids:
            all_approved = await self.outsource_quotes.list_all_approved()
            # 按 part_id 分组，按 (created_at desc, id desc) 取最新一条
            for q in all_approved:
                if q.process_id not in approval_proc_ids:
                    continue
                existing = approved_by_part.get(q.part_id)
                if existing is None or (
                    q.created_at > existing.created_at
                    or (q.created_at == existing.created_at and q.id > existing.id)
                ):
                    approved_by_part[q.part_id] = q
        approval_part_ids = list(approved_by_part.keys())

        # 4. customer 展开
        customer_ids_in: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is None:
                return OutsourceSendableListOut(
                    items=[], total=0, limit=limit, offset=offset,
                )
            customer_ids_in = await expand_customer_ids(self.customers, cid_int)

        kw = (keyword or "").strip() or None

        # 5. 并发查直发 + 审批 + 计数（按批次行；limit*2 给合并留余量）
        big_limit = max(limit * 2, 100)
        big_offset = max(offset - limit, 0)
        direct_rows: list[tuple[TPartBatch, TPart]] = []
        approval_rows: list[tuple[TPartBatch, TPart]] = []
        direct_total = 0
        approval_total = 0
        if direct_proc_to_active_companies:
            sendable_direct_ids = list(direct_proc_to_active_companies.keys())
            direct_total = await self.parts.count_direct_outsource_sendable(
                customer_ids_in=customer_ids_in, keyword=kw,
                process_ids=sendable_direct_ids,
            )
            if direct_total:
                direct_rows = await self.parts.list_direct_outsource_sendable(
                    customer_ids_in=customer_ids_in, keyword=kw,
                    process_ids=sendable_direct_ids,
                    limit=big_limit, offset=big_offset,
                )
        if approval_part_ids:
            approval_total = await self.parts.count_approved_outsource_sendable(
                part_ids=approval_part_ids, process_ids=list(approval_proc_ids),
                customer_ids_in=customer_ids_in, keyword=kw,
            )
            if approval_total:
                approval_rows = await self.parts.list_approved_outsource_sendable(
                    part_ids=approval_part_ids, process_ids=list(approval_proc_ids),
                    customer_ids_in=customer_ids_in, keyword=kw,
                    limit=big_limit, offset=big_offset,
                )

        # 6. 拼装 + 合并排序（行=批次）
        cust_cache = await preload_customer_cache(self.customers, [
            p.customer_id for _, p in direct_rows
            if p.customer_id is not None
        ] + [
            p.customer_id for _, p in approval_rows
            if p.customer_id is not None
        ])
        items: list[OutsourceSendableItem] = []

        # 6a. DIRECT items（按批次）
        for batch, p in direct_rows:
            cids = direct_proc_to_active_companies.get(batch.next_process_id, [])
            company_opts = [
                DirectOutsourceCompanyOption(
                    id=cid,
                    name=active_company_map[cid].name if cid in active_company_map else "",
                )
                for cid in cids
            ]
            if not company_opts:
                continue
            next_proc = process_map.get(batch.next_process_id)
            customer_path: str | None = None
            if p.customer_id is not None and p.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[p.customer_id], cust_cache,
                )
            items.append(OutsourceSendableItem(
                version=batch.version,  # OCC 在批次上；前端发送时回传
                send_mode="DIRECT",
                source_status=batch.status,  # PENDING 或 IN_PROCESS
                part_id=p.id,
                part_serial_no=p.serial_no,
                part_drawing_no=p.drawing_no,
                part_name=p.name,
                quantity=batch.quantity,  # 行=批次，quantity=批次量
                batch_id=batch.id,
                batch_no=batch.batch_no,
                batch_quantity=batch.quantity,
                planned_delivery_date=(
                    p.planned_delivery_date.isoformat()
                    if p.planned_delivery_date else None
                ),
                is_urgent=bool(getattr(p, "is_urgent", False)),
                customer_path=customer_path,
                next_process_id=batch.next_process_id,
                next_process_name=next_proc.name if next_proc else None,
                outsource_company_id=None,
                outsource_company_name=None,
                company_options=company_opts,
                price=None,
                status_label="sendable",
            ))

        # 6b. APPROVAL items（按批次）
        # 审批端需要的 company_id/name：从 APPROVED 报价拿
        approval_company_ids = {
            q.outsource_company_id for q in approved_by_part.values()
        }
        approval_company_map: dict[int, object] = {}
        if approval_company_ids:
            comps = await self.outsource_companies.list_by_ids(list(approval_company_ids))
            approval_company_map = {c.id: c for c in comps}
        for batch, p in approval_rows:
            q = approved_by_part.get(p.id)
            if q is None:
                continue
            company = approval_company_map.get(q.outsource_company_id)
            next_proc = process_map.get(q.process_id)
            customer_path: str | None = None
            if p.customer_id is not None and p.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[p.customer_id], cust_cache,
                )
            items.append(OutsourceSendableItem(
                version=batch.version,  # OCC 在批次上
                send_mode="APPROVAL",
                source_status=batch.status,
                part_id=p.id,
                part_serial_no=p.serial_no,
                part_drawing_no=p.drawing_no,
                part_name=p.name,
                quantity=batch.quantity,
                batch_id=batch.id,
                batch_no=batch.batch_no,
                batch_quantity=batch.quantity,
                planned_delivery_date=(
                    p.planned_delivery_date.isoformat()
                    if p.planned_delivery_date else None
                ),
                is_urgent=bool(getattr(p, "is_urgent", False)),
                customer_path=customer_path,
                next_process_id=batch.next_process_id,
                next_process_name=next_proc.name if next_proc else None,
                outsource_company_id=q.outsource_company_id,
                outsource_company_name=company.name if company else None,
                company_options=[],
                price=q.price,
                status_label="sendable",
            ))

        # 合并排序：加急 DESC, planned_delivery_date ASC, part_id DESC
        # 用统一 tuple 类型确保 Python sort 稳定
        items.sort(
            key=lambda it: (
                0 if it.is_urgent else 1,
                it.planned_delivery_date or "",
                -int(it.part_id),
            )
        )

        # 切分页（基于合并后的 total）
        total = direct_total + approval_total
        # 切片按 offset/limit（数据已经按统一顺序排好）
        page = items[max(0, offset - big_offset): max(0, offset - big_offset) + limit]

        return OutsourceSendableListOut(
            items=page, total=total, limit=limit, offset=offset,
        )

    async def list_direct_outsource_candidates(
        self,
        *,
        keyword: str | None = None,
        customer_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DirectOutsourceCandidateListOut:
        """旧版直接发送候选（已弃用；2026-07-28 后由 list_outsource_sendable 取代）。

        保留以兼容老调用方；新代码请用 list_outsource_sendable。
        """
        unified = await self.list_outsource_sendable(
            keyword=keyword, customer_id=customer_id,
            limit=limit, offset=offset,
        )
        # 过滤 DIRECT 行，转成旧 DirectOutsourceCandidateItem
        items = [
            DirectOutsourceCandidateItem(
                version=it.version,
                part_id=it.part_id,
                part_serial_no=it.part_serial_no,
                part_drawing_no=it.part_drawing_no,
                part_name=it.part_name,
                quantity=it.quantity,
                planned_delivery_date=it.planned_delivery_date,
                is_urgent=it.is_urgent,
                customer_path=it.customer_path,
                next_process_id=it.next_process_id,
                next_process_name=it.next_process_name,
                requires_approval=False,
                status_label="sendable",
                company_options=it.company_options,
            )
            for it in unified.items if it.send_mode == "DIRECT"
        ]
        return DirectOutsourceCandidateListOut(
            items=items, total=unified.total, limit=limit, offset=offset,
        )

    async def pass_inspection(
        self, part_id: int, *, batch_id: int | None = None,
        quantity: int | None = None,
    ) -> PartOut:
        """INSPECTION -> READY_TO_SHIP：品检合格。

        2026-07-29 批次化：可选 batch_id / quantity（部分通过先拆再过）。
        """
        part = await self._get_part_or_404(part_id)
        batch = await self._resolve_target_batch(
            part, batch_id,
            expect=lambda b: b.status == "INSPECTION",
            action="品检通过",
        )
        target = await self._maybe_split(part, batch, quantity)
        target.sm.pass_inspection(
            event_repo=self.events, created_by=self._user_id,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def deliver(
        self,
        part_id: int,
        *,
        actual_delivery_date: date | None = None,
        worker_badge_code: str | None = None,
        batch_id: int | None = None,
        quantity: int | None = None,
    ) -> PartOut:
        """READY_TO_SHIP -> DELIVERED：发货。

        - 文员/管理员手动调用：worker_badge_code=None → 走原通用路径，
          不写 actual_delivery_date。
        - 扫码台司机调用：worker_badge_code 必填；service 层校验
          `t_worker.work_type.code == '送货司机'` 且 is_active；写入
          `part.actual_delivery_date = today()` 与 PartEvent.worker_id / badge_code。
        - 2026-07-29 批次化：可选 batch_id / quantity（部分送货先拆再发）；
          part.actual_delivery_date 由 rollup 在全部活跃批次 DELIVERED 时写入，
          显式传 actual_delivery_date（文员补录）优先。

        实际送货日期入参用于 CLERK 补录（默认 None 即「今天」）。
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        driver: TWorker | None = None
        if worker_badge_code:
            worker = await self.workers.get_by_badge_code(worker_badge_code)
            if worker is None:
                raise BizError(
                    code=ErrCode.BIZ_WORKER_NOT_FOUND,
                    message=f"worker badge_code={worker_badge_code!r} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if not worker.is_active:
                raise BizError(
                    code=ErrCode.BIZ_WORKER_INACTIVE,
                    message=f"worker badge_code={worker_badge_code!r} is inactive",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            # 校验工种必须是「送货司机」
            wt = (
                await self.work_types.get_by_id(worker.work_type_id)
                if self.work_types and worker.work_type_id
                else None
            )
            if wt is None or wt.code != "送货司机":
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"worker badge_code={worker_badge_code!r} "
                        f"work_type_code={wt.code if wt else None!r}; "
                        f"deliver requires work_type='送货司机'"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            driver = worker

        if actual_delivery_date is not None:
            part.actual_delivery_date = actual_delivery_date

        # 2026-07-29 批次化：定位 READY_TO_SHIP 批次（部分送货先拆再发）。
        batch = await self._resolve_target_batch(
            part, batch_id,
            expect=lambda b: b.status == "READY_TO_SHIP",
            action="发货",
        )
        target = await self._maybe_split(part, batch, quantity)
        target.sm.deliver(
            worker=driver, event_repo=self.events,
            created_by=self._user_id,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def complete(
        self, part_id: int, *, batch_id: int | None = None,
    ) -> PartOut:
        """DELIVERED -> COMPLETED：确认完成。

        2026-07-29 批次化：
        - batch_id 指定 → 完成该 DELIVERED 批次；
        - 缺省 → 完成**全部** DELIVERED 批次（与原「整单完成」语义对齐）；
        - 全部批次终态后由 rollup 把工单置 COMPLETED 并释放流水号。
        """
        part = await self._get_part_or_404(part_id)
        batches = await self._batches().list_by_part(part.id)
        if batch_id is not None:
            targets = [b for b in batches if b.id == batch_id]
            if not targets:
                raise BizError(
                    code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
                    message=f"batch {batch_id} 不属于工单 {part_id} 或不存在",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
        else:
            targets = [b for b in batches if b.status == "DELIVERED"]
        if not targets:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=f"part {part_id} 没有 DELIVERED 状态的批次可完成",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        for target in targets:
            target.sm.complete(
                event_repo=self.events, created_by=self._user_id,
            )
            target.updated_by = self._user_id
            await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def start_repair(
        self, part_id: int, *, batch_id: int | None = None,
        quantity: int | None = None,
    ) -> PartOut:
        """-> REPAIRING：开始返修（从 INSPECTION / READY_TO_SHIP / DELIVERED 进入）。

        2026-07-29 批次化：可选 batch_id / quantity（部分返修先拆再转）。
        """
        part = await self._get_part_or_404(part_id)
        batch = await self._resolve_target_batch(
            part, batch_id,
            expect=lambda b: b.status in ("INSPECTION", "READY_TO_SHIP", "DELIVERED"),
            action="开始返修",
        )
        target = await self._maybe_split(part, batch, quantity)
        # PR-M 2026-08-04: 标记工单 + 当前批次为「曾返修」;
        # 贯穿到 COMPLETED/CANCELLED 之后仍可见, 便于列表 / 打印区分返修件.
        target.has_been_repaired = True
        part.has_been_repaired = True
        target.sm.start_repair(
            event_repo=self.events, created_by=self._user_id,
        )
        target.updated_by = self._user_id
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def complete_repair(
        self, part_id: int, shelf_id: int, *,
        batch_id: int | None = None,
        next_process_id: int | None = None,
    ) -> PartOut:
        """REPAIRING -> IN_PROCESS / INSPECTION (PR-M 2026-08-04)：返修完成，放回生产货架。

        2026-07-17：补 shelf↔process 校验——REPAIRING 期间 `next_process_id`
        由 start_repair 透传保留（ON_SHELF 进入时不传 process，next_process_id
        沿用 REPAIRING 之前）；如果 caller 选了不兼容的 shelf，422 拒绝。
        `next_process_id IS NULL`（fail_inspection 已清空）时跳过校验。

        2026-07-29 批次化：可选 batch_id（默认唯一 REPAIRING 批次）；
        shelf↔process 校验读批次的 next_process_id。
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        shelf = await self.shelves.get_by_id(shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not shelf.is_active:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf {shelf.code!r} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if shelf.zone not in (
            ShelfZone.PRODUCTION.value,
            ShelfZone.INSPECTION.value,
        ):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"shelf {shelf.code!r} zone={shelf.zone!r}; "
                    "complete_repair requires PRODUCTION or INSPECTION"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        batch = await self._resolve_target_batch(
            part, batch_id,
            expect=lambda b: b.status == "REPAIRING",
            action="完成返修",
        )
        # PR-M 2026-08-04: caller explicit next_process_id 覆盖 carried; 缺省沿用 batch 值
        effective_next_pid = next_process_id or batch.next_process_id
        if shelf.zone == ShelfZone.PRODUCTION.value:
            if effective_next_pid is not None:
                carried_process = await self._get_process(effective_next_pid)
                await self._assert_shelf_maps_process(shelf, carried_process)
            batch.next_process_id = effective_next_pid
            batch.sm.complete_repair(
                shelf=shelf, event_repo=self.events,
                created_by=self._user_id,
            )
        else:
            # INSPECTION zone: 走新 transition complete_repair_to_inspection, 无需 process 校验
            batch.sm.complete_repair_to_inspection(
                target_shelf=shelf, event_repo=self.events,
                created_by=self._user_id,
            )
        batch.updated_by = self._user_id
        await self._batches().update(batch)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def repair_dispatch(
        self, part_id: int, shelf_id: int, *,
        batch_id: int | None = None,
        quantity: int | None = None,
        next_process_id: int | None = None,
    ) -> PartOut:
        """PR-M 2026-08-04 续：一步式返修下发（DELIVERED → REPAIRING → ON_SHELF/INSPECTION）。

        单 session 单 commit；保留两条 PartEvent（REPAIR_STARTED + REPAIR_COMPLETED）；
        入口白名单与现有 start_repair 一致（INSPECTION / READY_TO_SHIP / DELIVERED）；
        shelf.zone-aware 分流（PRODUCTION → ON_SHELF；INSPECTION → INSPECTION）。

        复用既有 helper；不再走 start_repair + complete_repair 两次 commit，
        因此 part 不会卡在 REPAIRING 中间状态。
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        shelf = await self.shelves.get_by_id(shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not shelf.is_active:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf {shelf.code!r} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if shelf.zone not in (
            ShelfZone.PRODUCTION.value,
            ShelfZone.INSPECTION.value,
        ):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"shelf {shelf.code!r} zone={shelf.zone!r}; "
                    "repair_dispatch requires PRODUCTION or INSPECTION"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        batch = await self._resolve_target_batch(
            part, batch_id,
            expect=lambda b: b.status in ("INSPECTION", "READY_TO_SHIP", "DELIVERED"),
            action="返修下发",
        )
        target = await self._maybe_split(part, batch, quantity)
        # PR-M 2026-08-04：标记返修件（工单 + 当前批次）
        target.has_been_repaired = True
        part.has_been_repaired = True
        # step 1: start_repair 状态机（任意入口 → REPAIRING）
        target.sm.start_repair(
            event_repo=self.events, created_by=self._user_id,
        )
        target.updated_by = self._user_id
        part.updated_by = self._user_id
        # step 2: complete_repair 状态机（REPAIRING → ON_SHELF 或 INSPECTION）
        effective_next_pid = next_process_id or target.next_process_id
        if shelf.zone == ShelfZone.PRODUCTION.value:
            if effective_next_pid is not None:
                carried_process = await self._get_process(effective_next_pid)
                await self._assert_shelf_maps_process(shelf, carried_process)
            target.next_process_id = effective_next_pid
            target.sm.complete_repair(
                shelf=shelf, event_repo=self.events,
                created_by=self._user_id,
            )
        else:
            # INSPECTION 走新 transition，无需 process 校验
            target.sm.complete_repair_to_inspection(
                target_shelf=shelf, event_repo=self.events,
                created_by=self._user_id,
            )
        target.updated_by = self._user_id
        await self.parts.update(part)
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def fail_inspection(
        self, part_id: int, data: FailInspectionRequest,
    ) -> PartOut:
        """INSPECTION -> IN_PROCESS：品检不通过，打回生产货架（含备注）。

        2026-07-21 改：
        - 接受 `FailInspectionRequest`（shelf_id + next_process_id + note）。
        - 通过 `_validate_production_shelf_and_process` 一次性校验 shelf 存在 /
          active / PRODUCTION + process 存在 + **`t_shelf_process` 映射**
          （与 `place_on_shelf` / `release_from_programming` 对齐，
          缺映射抛 `BIZ_SHELF_PROCESS_NOT_MAPPED` 422）。
        - **保留** `part.next_process_id = data.next_process_id`（旧逻辑是清空，
          由文员 place-on-shelf 再选；现在品检员一并指定，工人可直接领取）。
        - note 通过 `sm.fail_inspection(..., note=data.note)` 透传到
          `on_fail_inspection` 回调，写入 `TPartEvent.note`，事件历史一览可见。
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        batch = await self._resolve_target_batch(
            part, self._parse_batch_id(data),
            expect=lambda b: b.status == PartStatus.INSPECTION.value,
            action="品检打回",
        )
        if batch.status != PartStatus.INSPECTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=(
                    f"batch {batch.batch_no} status={batch.status!r}; "
                    f"fail_inspection requires INSPECTION"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        shelf_id_int = parse_snowflake_id(data.shelf_id, field_name="shelf_id")
        process_id_int = parse_snowflake_id(
            data.next_process_id, field_name="next_process_id",
        )
        shelf, process = await self._validate_production_shelf_and_process(
            shelf_id_int, process_id_int,
        )
        target = await self._maybe_split(
            part, batch, getattr(data, "quantity", None),
        )
        target.next_process_id = process.id  # 保留 inspector 指定的下一道工序
        target.sm.fail_inspection(
            shelf=shelf, process=process, event_repo=self.events,
            created_by=self._user_id,
            note=data.note,
        )
        target.updated_by = self._user_id
        await self._batches().update(target)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    async def cancel(
        self, part_id: int, *, batch_id: int | None = None,
    ) -> PartOut:
        """-> CANCELLED：取消（级联），释放流水号。

        2026-07-29 批次化：
        - batch_id 指定 → 仅取消该批次（须非终态）；其余批次不受影响；
        - 缺省 → 级联取消**全部非终态**批次（与原「整单取消」语义对齐）；
        - 全部批次终态后由 rollup 把工单置 CANCELLED/COMPLETED 并释放流水号。
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        batches = await self._batches().list_by_part(part.id)
        if batch_id is not None:
            targets = [b for b in batches if b.id == batch_id]
            if not targets:
                raise BizError(
                    code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
                    message=f"batch {batch_id} 不属于工单 {part_id} 或不存在",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if targets[0].status in self._TERMINAL_STATUSES:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_TRANSITION,
                    message=(
                        f"批次 {targets[0].batch_no} 已是终态 "
                        f"{targets[0].status}，不能取消"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
        else:
            targets = [
                b for b in batches if b.status not in self._TERMINAL_STATUSES
            ]
        if not targets:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=f"part {part_id} 没有可取消的非终态批次",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        for target in targets:
            # 2026-07-30：先记录原状态，再 cancel（cancel 后状态变为 CANCELLED）
            was_outsource = target.status == PartStatus.OUTSOURCE.value
            target.sm.cancel(
                event_repo=self.events, created_by=self._user_id,
            )
            target.updated_by = self._user_id
            await self._batches().update(target)
            if was_outsource and self.outsource_shipments is not None:
                shipment = await self.outsource_shipments.get_open_by_batch_id(target.id)
                if shipment is not None:
                    shipment.status = "CANCELLED"
                    shipment.updated_by = self._user_id
                    await self.outsource_shipments.update(shipment)
        await self._after_batch_transition(part)
        items = await self._to_out([part])
        return items[0]

    # ============================================================
    # 批次监控 / 手动拆分 / 批次取消（2026-07-29）
    # ============================================================
    async def list_batches(self, part_id: int) -> list["PartBatchOut"]:
        """工单的全部批次（详情页批次监控卡片）。"""
        from schema.part import PartBatchOut

        part = await self._get_part_or_404(part_id)
        batches = await self._batches().list_by_part(part.id)
        if not batches:
            return []

        # holder / 工序 / 送货单 名称批查
        shelf_ids = [
            int(b.current_holder_id) for b in batches
            if b.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF")
            and b.current_holder_id
        ]
        shelf_map: dict[int, str] = {}
        if shelf_ids:
            shelf_map = {s.id: s.code for s in await self.shelves.list_by_ids(shelf_ids)}
        worker_ids = [
            int(b.current_holder_id) for b in batches
            if b.location == "WORKER" and b.current_holder_id
        ]
        worker_map: dict[int, str] = {}
        if worker_ids:
            worker_map = {w.id: w.name for w in await self.workers.list_by_ids(worker_ids)}
        company_ids = [
            int(b.current_holder_id) for b in batches
            if b.location == "OUTSOURCE_COMPANY" and b.current_holder_id
        ]
        company_map: dict[int, str] = {}
        if company_ids and self.outsource_companies is not None:
            company_map = {
                c.id: c.name
                for c in await self.outsource_companies.list_by_ids(company_ids)
            }
        process_ids = {int(b.next_process_id) for b in batches if b.next_process_id}
        process_map: dict[int, str] = {}
        if process_ids and self.processes is not None:
            process_map = {
                pr.id: pr.name
                for pr in await self.processes.list_by_ids(list(process_ids))
            }
        note_ids = {int(b.delivery_note_id) for b in batches if b.delivery_note_id}
        note_map: dict[int, str] = {}
        if note_ids and self.delivery_notes_repo is not None:
            notes = await self.delivery_notes_repo.list_by_ids(list(note_ids))
            note_map = {n.id: n.delivery_note_no for n in notes}

        out: list[PartBatchOut] = []
        for b in batches:
            holder_display: str | None = None
            if b.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF") and b.current_holder_id:
                code = shelf_map.get(int(b.current_holder_id))
                if code:
                    prefix = "品检 " if b.location == "INSPECTION_SHELF" else ""
                    holder_display = f"货架 {prefix}{code}"
            elif b.location == "WORKER" and b.current_holder_id:
                name = worker_map.get(int(b.current_holder_id))
                holder_display = f"工人 {name}" if name else None
            elif b.location == "OUTSOURCE_COMPANY" and b.current_holder_id:
                name = company_map.get(int(b.current_holder_id))
                holder_display = f"外协 {name}" if name else None
            elif b.location == "OFFICE":
                holder_display = "编程员持有" if b.status == "PROGRAMMING" else "办公室"

            out.append(PartBatchOut(
                id=b.id,
                version=b.version,
                part_id=b.part_id,
                batch_no=b.batch_no,
                batch_label=(
                    f"{part.serial_no}B{b.batch_no:02d}"
                    if part.serial_no else f"批次{b.batch_no}"
                ),
                quantity=b.quantity,
                status=b.status,
                location=b.location,
                current_holder_id=b.current_holder_id,
                current_holder_display=holder_display,
                next_process_id=b.next_process_id,
                next_process_name=(
                    process_map.get(int(b.next_process_id))
                    if b.next_process_id else None
                ),
                placed_at=b.placed_at,
                delivery_note_id=b.delivery_note_id,
                delivery_note_no=(
                    note_map.get(int(b.delivery_note_id))
                    if b.delivery_note_id else None
                ),
                parent_batch_id=b.parent_batch_id,
                created_at=b.created_at,
                updated_at=b.updated_at,
            ))
        return out

    async def split_batch(
        self, part_id: int, *, batch_id: int, quantity: int,
    ) -> list["PartBatchOut"]:
        """手动拆分批次（详情页操作）：源批次必须非终态。

        拆分只移动数量，不改变状态；返回最新批次列表。
        """
        part = await self._get_part_or_404(part_id)
        batch = await self._resolve_target_batch(part, batch_id, action="拆分")
        if batch.status in self._TERMINAL_STATUSES:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=f"批次 {batch.batch_no} 已是终态 {batch.status}，不能拆分",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        await self._split_batch(part, batch, quantity)
        await self._broadcast()
        return await self.list_batches(part_id)

    async def cancel_batch(
        self, part_id: int, *, batch_id: int,
    ) -> list["PartBatchOut"]:
        """取消单个批次（详情页操作）；返回最新批次列表。"""
        await self.cancel(part_id, batch_id=batch_id)
        return await self.list_batches(part_id)

    async def _check_parent_assembly(self, part: TPart) -> None:
        """Part 状态变更后，rollup 父装配件状态（2026-08-03：派生态 helper 化）。

        所有派生规则集中在 ``service/_assembly_rollup.py``；本函数只负责：
        - 拉父装配件（含 deleted_at 过滤，与旧实现对齐）
        - 委托给 ``recompute_assembly_status``
        """
        if part.assembly_id is None:
            return
        from model.assembly import TAssembly
        from service._assembly_rollup import recompute_assembly_status

        session = self.parts.session
        from sqlalchemy import select

        stmt = select(TAssembly).where(
            TAssembly.id == part.assembly_id,
            TAssembly.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        assembly = result.scalar_one_or_none()
        if assembly is None:
            return

        await recompute_assembly_status(
            session=session,
            assembly=assembly,
            parts=self.parts,
            user_id=self._user_id,
        )

    # ============================================================
    # 内部：拼客户路径 + location 字段 + 多态 holder
    # ============================================================
    async def _to_out(self, rows: list[TPart]) -> list[PartOut]:
        if not rows:
            return []
        cust_ids = list({p.customer_id for p in rows})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map: dict[int, TCustomer] = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = (
            await self.customers.list_by_ids(parent_ids) if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {p.id: p for p in parents}

        # 批查 worker name（location=WORKER 的行）
        worker_ids = [
            int(p.current_holder_id)
            for p in rows
            if p.location == "WORKER" and p.current_holder_id
        ]
        worker_map: dict[int, str] = {}
        if worker_ids:
            workers = await self.workers.list_by_ids(worker_ids)
            worker_map = {w.id: w.name for w in workers}

        # 批查外协公司名（location=OUTSOURCE_COMPANY 的行，2026-07-15）
        outsource_company_ids = [
            int(p.current_holder_id)
            for p in rows
            if p.location == "OUTSOURCE_COMPANY" and p.current_holder_id
        ]
        outsource_company_map: dict[int, str] = {}
        if outsource_company_ids and self.outsource_companies is not None:
            companies = await self.outsource_companies.list_by_ids(
                outsource_company_ids,
            )
            outsource_company_map = {c.id: c.name for c in companies}

        # 批查下一道工序名称（避免前端为「下一道工序列」再发一次 /processes 请求）
        process_ids = {
            int(p.next_process_id)
            for p in rows
            if p.next_process_id
        }
        process_map: dict[int, str] = {}
        if process_ids and self.processes is not None:
            procs = await self.processes.list_by_ids(list(process_ids))
            process_map = {pr.id: pr.name for pr in procs}

        # 批查所属送货单（PR-G 2026-07-22）：detail 页需要单号 + 状态
        # 2026-07-29 批次化：t_part.delivery_note_id 已停写（保留历史数据），
        # 改从活跃批次的 delivery_note_id 解析（一单多件时取第一张单号展示）。
        part_note_ids: dict[int, int] = {}
        for p in rows:
            if p.delivery_note_id:
                part_note_ids[p.id] = int(p.delivery_note_id)
        if self.part_batches is not None:
            for b in await self.part_batches.list_active_by_part_ids(
                [p.id for p in rows]
            ):
                if b.delivery_note_id and b.part_id not in part_note_ids:
                    part_note_ids[b.part_id] = int(b.delivery_note_id)
        delivery_note_ids = set(part_note_ids.values())
        delivery_note_map: dict[int, tuple[str, str]] = {}
        if delivery_note_ids and self.delivery_notes_repo is not None:
            notes = await self.delivery_notes_repo.list_by_ids(
                list(delivery_note_ids),
            )
            delivery_note_map = {
                n.id: (n.delivery_note_no, n.status) for n in notes
            }

        out: list[PartOut] = []
        for p in rows:
            cust = cust_map.get(p.customer_id)
            parent = (
                parent_map.get(cust.parent_id)
                if cust and cust.parent_id
                else None
            )
            parent_name = parent.name if parent else None
            child_name = cust.name if cust else None
            path: str | None = None
            if parent_name and child_name:
                path = f"{parent_name} / {child_name}"
            elif child_name:
                path = child_name
            elif parent_name:
                path = parent_name

            # Use location field instead of dual-table holder lookup
            holder_kind: str | None = None
            shelf_code: str | None = None
            worker_name: str | None = None
            outsource_company_name: str | None = None
            if p.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF") and p.current_holder_id:
                holder_kind = "shelf"
                shelf_id = int(p.current_holder_id)
                shelves_for_holder = await self.shelves.list_by_ids([shelf_id])
                if shelves_for_holder:
                    shelf_code = shelves_for_holder[0].code
            elif p.location == "WORKER" and p.current_holder_id:
                holder_kind = "worker"
                worker_name = worker_map.get(int(p.current_holder_id))
            elif p.location == "OUTSOURCE_COMPANY" and p.current_holder_id:
                holder_kind = "outsource_company"
                outsource_company_name = outsource_company_map.get(
                    int(p.current_holder_id),
                )

            # 所在位置的人类可读描述（2026-07-11 装配体子件表使用）
            holder_display: str | None = None
            if shelf_code is not None:
                prefix = "品检 " if p.location == "INSPECTION_SHELF" else ""
                holder_display = f"货架 {prefix}{shelf_code}"
            elif worker_name is not None:
                holder_display = f"工人 {worker_name}"
            elif outsource_company_name is not None:
                holder_display = f"外协 {outsource_company_name}"
            elif p.location == "OFFICE":
                holder_display = "编程员持有"

            out.append(
                PartOut(
                    id=p.id,
                    version=p.version,
                    serial_no=p.serial_no,
                    name=p.name,
                    drawing_no=p.drawing_no,
                    quantity=p.quantity,
                    planned_delivery_date=p.planned_delivery_date,
                    actual_delivery_date=p.actual_delivery_date,
                    is_urgent=p.is_urgent,
                    status=_parse_status(p.status) or PartStatus.PENDING,
                    order_no=p.order_no,
                    system_delivery_date=p.system_delivery_date,
                    note=p.note,
                    customer_name=child_name,
                    parent_customer_name=parent_name,
                    customer_path=path,
                    assembly_id=p.assembly_id,
                    delivery_note_id=part_note_ids.get(p.id),
                    delivery_note_no=(
                        delivery_note_map.get(part_note_ids[p.id])[0]
                        if p.id in part_note_ids
                        and delivery_note_map.get(part_note_ids[p.id])
                        else None
                    ),
                    delivery_note_status=(
                        delivery_note_map.get(part_note_ids[p.id])[1]
                        if p.id in part_note_ids
                        and delivery_note_map.get(part_note_ids[p.id])
                        else None
                    ),
                    current_holder_id=p.current_holder_id,
                    current_holder_kind=holder_kind,
                    shelf_code=shelf_code,
                    placed_at=getattr(p, "placed_at", None),
                    location=p.location,
                    worker_name=worker_name,
                    outsource_company_name=outsource_company_name,
                    current_holder_display=holder_display,
                    next_process_id=p.next_process_id,
                    next_process_name=process_map.get(int(p.next_process_id))
                    if p.next_process_id
                    else None,
                    # 2026-07-21：transient 属性，仅 list_for_work_type* 路径会填；
                    # 其它 ORM（如 place_on_shelf、cancel、pick_up）getattr 默认 None。
                    last_inspection_fail_note=getattr(p, "last_inspection_fail_note", None),
                    # 2026-08-04 「返修接收」PR-M：返修件标识
                    has_been_repaired=bool(getattr(p, "has_been_repaired", False)),
                )
            )
        return out

    async def _to_batch_out(
        self, rows: list[tuple[TPartBatch, TPart]],
    ) -> list[PartOut]:
        """批次级列表输出（2026-07-29）：展示字段来自工单，状态/数量/位置来自批次。

        用于扫码台可领列表 / 工人持有列表 / 品检待办等「行=批次」的场景：
        - PartOut.quantity = 批次量；status/location/holder/next_process = 批次值；
        - batch_id / batch_no / batch_label 填充供请求回传与展示。
        """
        if not rows:
            return []
        base = await self._to_out([p for _, p in rows])
        base_map = {o.id: o for o in base}

        # 批次级 holder / 工序名称批查（与 _to_out 同路径，主体换成批次）
        shelf_ids = [
            int(b.current_holder_id) for b, _ in rows
            if b.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF")
            and b.current_holder_id
        ]
        shelf_map: dict[int, str] = {}
        if shelf_ids:
            shelf_map = {s.id: s.code for s in await self.shelves.list_by_ids(shelf_ids)}
        worker_ids = [
            int(b.current_holder_id) for b, _ in rows
            if b.location == "WORKER" and b.current_holder_id
        ]
        worker_map: dict[int, str] = {}
        if worker_ids:
            worker_map = {w.id: w.name for w in await self.workers.list_by_ids(worker_ids)}
        company_ids = [
            int(b.current_holder_id) for b, _ in rows
            if b.location == "OUTSOURCE_COMPANY" and b.current_holder_id
        ]
        company_map: dict[int, str] = {}
        if company_ids and self.outsource_companies is not None:
            company_map = {
                c.id: c.name
                for c in await self.outsource_companies.list_by_ids(company_ids)
            }
        process_ids = {int(b.next_process_id) for b, _ in rows if b.next_process_id}
        process_map: dict[int, str] = {}
        if process_ids and self.processes is not None:
            process_map = {
                pr.id: pr.name
                for pr in await self.processes.list_by_ids(list(process_ids))
            }

        out: list[PartOut] = []
        for b, p in rows:
            o = base_map[p.id]
            holder_kind: str | None = None
            shelf_code: str | None = None
            worker_name: str | None = None
            company_name: str | None = None
            if b.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF") and b.current_holder_id:
                holder_kind = "shelf"
                shelf_code = shelf_map.get(int(b.current_holder_id))
            elif b.location == "WORKER" and b.current_holder_id:
                holder_kind = "worker"
                worker_name = worker_map.get(int(b.current_holder_id))
            elif b.location == "OUTSOURCE_COMPANY" and b.current_holder_id:
                holder_kind = "outsource_company"
                company_name = company_map.get(int(b.current_holder_id))

            holder_display: str | None = None
            if shelf_code is not None:
                prefix = "品检 " if b.location == "INSPECTION_SHELF" else ""
                holder_display = f"货架 {prefix}{shelf_code}"
            elif worker_name is not None:
                holder_display = f"工人 {worker_name}"
            elif company_name is not None:
                holder_display = f"外协 {company_name}"
            elif b.location == "OFFICE":
                holder_display = "编程员持有"

            out.append(o.model_copy(update={
                "batch_id": b.id,
                "batch_no": b.batch_no,
                "batch_label": (
                    f"{p.serial_no}B{b.batch_no:02d}"
                    if p.serial_no else f"批次{b.batch_no}"
                ),
                "quantity": b.quantity,
                "status": _parse_status(b.status) or PartStatus.PENDING,
                "location": b.location,
                "current_holder_id": b.current_holder_id,
                "current_holder_kind": holder_kind,
                "shelf_code": shelf_code,
                "worker_name": worker_name,
                "outsource_company_name": company_name,
                "current_holder_display": holder_display,
                "next_process_id": b.next_process_id,
                "next_process_name": (
                    process_map.get(int(b.next_process_id))
                    if b.next_process_id else None
                ),
                "placed_at": b.placed_at,
                # 2026-08-04 「返修接收」PR-M：批次级返修标识
                # 优先取批次标记（部分返修拆分时新批次独立计），无则继承工单标记
                "has_been_repaired": bool(
                    getattr(b, "has_been_repaired", False)
                    or getattr(p, "has_been_repaired", False)
                ),
            }))
        return out

    async def _to_list_out(self, rows: list[TPart]) -> list[PartListItem]:
        """窄版 _to_out：列表展示用，省去 next_process 批查 / assembly_id / 多态 holder。

        复用 customers + workers + shelves 的批查路径；性能上比 _to_out
        省一次 processes.list_by_ids 调用。
        """
        if not rows:
            return []
        cust_ids = list({p.customer_id for p in rows})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map: dict[int, TCustomer] = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = (
            await self.customers.list_by_ids(parent_ids) if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {p.id: p for p in parents}

        worker_ids = [
            int(p.current_holder_id)
            for p in rows
            if p.location == "WORKER" and p.current_holder_id
        ]
        worker_map: dict[int, str] = {}
        if worker_ids:
            workers = await self.workers.list_by_ids(worker_ids)
            worker_map = {w.id: w.name for w in workers}

        # 一次性批查所有需要查货架 code 的 id
        shelf_ids = [
            int(p.current_holder_id)
            for p in rows
            if p.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF")
            and p.current_holder_id
        ]
        shelf_map: dict[int, str] = {}
        if shelf_ids:
            shelf_rows = await self.shelves.list_by_ids(shelf_ids)
            shelf_map = {s.id: s.code for s in shelf_rows}

        # 2026-07-28 PR-H：批查 next_process_id 对应的 process.name（picker 自动填工序用）
        next_process_ids = list({
            int(p.next_process_id) for p in rows if p.next_process_id
        })
        process_map: dict[int, str] = {}
        if next_process_ids:
            proc_rows = await self.processes.list_by_ids(next_process_ids)
            process_map = {pr.id: pr.name for pr in proc_rows}

        out: list[PartListItem] = []
        for p in rows:
            cust = cust_map.get(p.customer_id)
            parent = (
                parent_map.get(cust.parent_id)
                if cust and cust.parent_id
                else None
            )
            parent_name = parent.name if parent else None
            child_name = cust.name if cust else None
            path: str | None = None
            if parent_name and child_name:
                path = f"{parent_name} / {child_name}"
            elif child_name:
                path = child_name
            elif parent_name:
                path = parent_name

            shelf_code: str | None = None
            worker_name: str | None = None
            if p.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF") and p.current_holder_id:
                shelf_code = shelf_map.get(int(p.current_holder_id))
            elif p.location == "WORKER" and p.current_holder_id:
                worker_name = worker_map.get(int(p.current_holder_id))

            # 所在位置的人类可读描述
            holder_display: str | None = None
            if shelf_code is not None:
                prefix = "品检 " if p.location == "INSPECTION_SHELF" else ""
                holder_display = f"货架 {prefix}{shelf_code}"
            elif worker_name is not None:
                holder_display = f"工人 {worker_name}"
            elif p.location == "OFFICE":
                holder_display = "编程员持有"

            out.append(
                PartListItem(
                    id=p.id,
                    version=p.version,
                    serial_no=p.serial_no,
                    name=p.name,
                    drawing_no=p.drawing_no,
                    applicant_name=p.applicant_name,
                    quantity=p.quantity,
                    unit_price=p.unit_price,
                    request_date=p.request_date,
                    planned_delivery_date=p.planned_delivery_date,
                    actual_delivery_date=p.actual_delivery_date,
                    is_urgent=p.is_urgent,
                    status=_parse_status(p.status) or PartStatus.PENDING,
                    order_no=p.order_no,
                    system_delivery_date=p.system_delivery_date,
                    note=p.note,
                    customer_name=child_name,
                    parent_customer_name=parent_name,
                    customer_path=path,
                    delivery_note_id=p.delivery_note_id,
                    location=p.location,
                    shelf_code=shelf_code,
                    worker_name=worker_name,
                    current_holder_display=holder_display,
                    # 2026-07-28 PR-H：picker 自动填工序
                    next_process_id=p.next_process_id,
                    next_process_name=(
                        process_map.get(int(p.next_process_id))
                        if p.next_process_id else None
                    ),
                    created_at=p.created_at,
                    # 2026-08-04 「返修接收」PR-M：一览返修标记
                    has_been_repaired=bool(getattr(p, "has_been_repaired", False)),
                )
            )
        return out

    async def _assemblies_to_list_items(self, assemblies: list[TAssembly]) -> list[PartListItem]:
        """将装配件列表转换为 PartListItem（装配体合并展示用）。"""
        if not assemblies:
            return []
        # 批查客户
        cust_ids = list({a.customer_id for a in assemblies})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map: dict[int, TCustomer] = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = await self.customers.list_by_ids(parent_ids) if parent_ids else []
        parent_map: dict[int, TCustomer] = {p.id: p for p in parents}

        # 批查子件数量
        from sqlalchemy import func, select

        ids = [a.id for a in assemblies]
        stmt = (
            select(TPart.assembly_id, func.count(TPart.id))
            .where(
                TPart.assembly_id.in_(ids),
                TPart.deleted_at.is_(None),
            )
            .group_by(TPart.assembly_id)
        )
        result = await self.parts.session.execute(stmt)
        child_counts = {row[0]: int(row[1]) for row in result.all()}

        out: list[PartListItem] = []
        for a in assemblies:
            cust = cust_map.get(a.customer_id)
            parent = parent_map.get(cust.parent_id) if cust and cust.parent_id else None
            parent_name = parent.name if parent else None
            child_name = cust.name if cust else None
            path: str | None = None
            if parent_name and child_name:
                path = f"{parent_name} / {child_name}"
            elif child_name:
                path = child_name
            elif parent_name:
                path = parent_name

            count = child_counts.get(a.id, 0)
            out.append(
                PartListItem(
                    id=a.id,
                    version=a.version,
                    serial_no=a.serial_no,
                    name=a.name,
                    drawing_no=a.drawing_no,
                    applicant_name=a.applicant_name,
                    quantity=a.quantity,
                    unit_price=a.unit_price,
                    total_price=a.total_price,
                    request_date=a.request_date,
                    planned_delivery_date=a.planned_delivery_date,
                    actual_delivery_date=a.actual_delivery_date,
                    is_urgent=a.is_urgent,
                    status=PartStatus(a.status),
                    order_no=a.order_no,
                    system_delivery_date=a.system_delivery_date,
                    note=a.note,
                    customer_name=child_name,
                    parent_customer_name=parent_name,
                    customer_path=path,
                    created_at=a.created_at,
                    row_type="ASSEMBLY",
                    has_children=count > 0,
                    child_count=count if count > 0 else None,
                )
            )
        return out
