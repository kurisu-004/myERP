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
from model import TAssembly, TCustomer, TPart, TPartEvent, TProcess, TShelf, TWorker, TWorkType
from model.enums import PartEventType, PartLocation, PartStatus, ProcessCategory, ShelfZone
from repository.applicant import ApplicantRepository
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.delivery_note import DeliveryNoteRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_company_process import OutsourceCompanyProcessRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.part_file import PartFileRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from schema.part import (
    FailInspectionRequest,
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
from service._id_parse import parse_snowflake_id
from service._session_refresh import refresh_for_state_machine
from service.applicant import ApplicantService
from service.part_file import PartFileService
from utils.id_gen import new_id

Broadcaster = Callable[[], Awaitable[None]]
EventBroadcaster = Callable[[str, dict], Awaitable[None]]


def _item_to_part_create_request(item: PartBatchTreeItem) -> PartCreateRequest:
    """PartBatchTreeItem → PartCreateRequest。`create_parts_tree` 调单页 PDF 时复用 `create_part` 走标准路径。"""
    return PartCreateRequest(
        name=item.name,
        drawing_no=item.drawing_no,
        applicant_name=item.applicant_name,
        applicant_id=item.applicant_id,
        quantity=item.quantity,
        unit_price=Decimal("0"),
        total_price=Decimal("0"),
        request_date=item.request_date,
        planned_delivery_date=item.planned_delivery_date,
        is_urgent=item.is_urgent,
        order_no=item.order_no,
        system_delivery_date=item.system_delivery_date,
        note=item.note,
        customer_id=item.customer_id,
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
        broadcaster: Broadcaster | None = None,
        event_broadcaster: EventBroadcaster | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.parts = parts
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
        self.outsource_quotes = outsource_quotes  # 2026-07-16：外协报价（send_to_outsource 防御 + mark_used）
        self.quote_events = quote_events  # 2026-07-16：外协报价事件（mark_used 写 USED 事件）
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
        rows = await self.parts.list_with_filters(
            customer_ids_in=customer_ids_in,
            statuses=query.statuses,
            is_urgent=query.is_urgent,
            keyword=query.keyword,
            order_no=query.order_no,
            has_outsource_history=query.has_outsource_history,
            request_date_from=query.request_date_from,
            request_date_to=query.request_date_to,
            planned_delivery_date_from=query.planned_delivery_date_from,
            planned_delivery_date_to=query.planned_delivery_date_to,
            system_delivery_date_from=query.system_delivery_date_from,
            system_delivery_date_to=query.system_delivery_date_to,
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
            has_outsource_history=query.has_outsource_history,
            request_date_from=query.request_date_from,
            request_date_to=query.request_date_to,
            planned_delivery_date_from=query.planned_delivery_date_from,
            planned_delivery_date_to=query.planned_delivery_date_to,
            system_delivery_date_from=query.system_delivery_date_from,
            system_delivery_date_to=query.system_delivery_date_to,
        )
        items = await self._to_list_out(rows)
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
        await self._write_event(
            part=part,
            event_type=PartEventType.CREATED,
            from_status=None,
            to_status=PartStatus.PENDING,
            created_by=self._user_id,
        )
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
        part_files: PartFileService,
        applicants: ApplicantService,
    ) -> PartBatchTreeResult:
        """批量树形创建：单页 PDF → 独立零件；多页 PDF → 装配件 + 子件。

        流程：
          1. 前置校验：customer / 多页约束 / master 数量；失败 → `failed` 列表，0 写盘。
          2. Excel 申请人兜底：applicant_id 缺 + name 非空 → bulk_get_or_create 回填。
          3. 主循环（按 pdf_index）：
             - 单页 → create_part + DRAWING 上传；
             - 多页 → acquire serial + 写 t_assembly + 逐 page 拆 + 写子件 + DRAWING 上传 +
               若有 master_item → ASSEMBLY_MASTER 上传。
          4. 事务边界：与 caller 共享 session；任一 upload 抛 BizError → 整批回滚。

        2026-07-21 新增。`POST /parts/batch-with-pdfs` 调用本方法。
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
                    unit_price=Decimal("0"),
                    total_price=Decimal("0"),
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

                f_out = await part_files.upload(
                    owner_id=child_id,
                    kind=PartFileKind.DRAWING,
                    data=page_bytes,
                    original_filename=f"{asm_drawing_no}_p{page.page_index + 1}.pdf",
                    content_type="application/pdf",
                )
                child_files.append(f_out)

                # 写 CREATED 事件
                await self.events.create(TPartEvent(
                    id=new_id(),
                    part_id=child_id,
                    worker_id=None,
                    event_type=PartEventType.CREATED.value,
                    from_status=None,
                    to_status=PartStatus.PENDING.value,
                    drawing_code=None,
                    badge_code=None,
                    note=None,
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
        if data.name is not None:
            part.name = data.name.strip()
        if data.drawing_no is not None:
            part.drawing_no = data.drawing_no.strip()
        if data.applicant_name is not None:
            part.applicant_name = data.applicant_name.strip()
        if data.quantity is not None:
            part.quantity = data.quantity
        if data.unit_price is not None:
            part.unit_price = data.unit_price
        if data.total_price is not None:
            part.total_price = data.total_price
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
        """
        part = await self._get_part_or_404(part_id)
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )

        # state machine handles status/location/holder mutation + event creation;
        # 状态机 on_enter_ON_SHELF 会同时把 next_process_id 设到 model 上。
        part.sm.place_on_shelf(
            shelf=shelf, process=process, event_repo=self.events,
            created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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

    async def send_to_programming(self, part_id: int) -> PartOut:
        """PENDING → PROGRAMMING：把零件发送至 CNC 编程。

        编程员在「待编程一览」看到这个零件，下载图纸/3D → 写程序 →
        上传 G 代码 → 在编程员端调用 `release_from_programming` 下发到货架。
        """
        part = await self._get_part_or_404(part_id)
        part.sm.send_to_programming(
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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
        """PENDING / ON_SHELF / WITH_WORKER → OUTSOURCE：把零件发送给外协公司。

        校验：
        - outsource_company_id 存在 + 未软删 + is_active=True
        - next_process_id 存在 + category=OUTSOURCE
        - 公司映射了该 OUTSOURCE 工序（t_outsource_company_process）

        note by design：当前实现与 place_on_shelf / pick_up_by_scan 同款风险——
        不带行锁、不带版本号。两位 CLERK 同时发送同一零件到不同公司时，
        可能产生双重 SENT_TO_OUTSOURCE 事件；与既有代码风险等级一致。
        """
        if self.outsource_companies is None or self.outsource_company_process is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing outsource repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        part = await self._get_part_or_404(part_id)
        await refresh_for_state_machine(
            self.parts.session,
            part,
            attrs=("status", "location", "next_process_id"),
        )

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

        # 5. [2026-07-16] 状态资格防御闸：UI 按钮也会校验，这里兜底
        next_process_obj = (
            await self._get_process(part.next_process_id)
            if part.next_process_id else None
        )
        next_cat = next_process_obj.category if next_process_obj else None
        allowed = (
            part.status == PartStatus.PENDING.value
            or (
                part.status == PartStatus.IN_PROCESS.value
                and part.location == PartLocation.PRODUCTION_SHELF.value
                and next_cat == ProcessCategory.OUTSOURCE.value
            )
        )
        if not allowed:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_OUTSOURCEABLE,
                message=(
                    f"零件状态 status={part.status} location={part.location} "
                    f"next_process.category={next_cat}，"
                    "不符合发送外协条件（仅 PENDING 或 IN_PROCESS + PRODUCTION_SHELF "
                    "+ 下一道=OUTSOURCE）"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 6. [2026-07-16] 已批报价防御闸
        if self.outsource_quotes is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing outsource quote repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        approved_quote = await self.outsource_quotes.get_one_approved(
            part_id=part.id,
            outsource_company_id=company_id_int,
            process_id=process_id_int,
        )
        if approved_quote is None:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_APPROVED,
                message=(
                    f"未找到「{company.name} / {process.code}」的已批准报价，"
                    "请先在「报价一览」中提交并由 MANAGER 审核通过"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        await refresh_for_state_machine(
            self.parts.session, approved_quote, attrs=("status",),
        )

        # 7. 状态机转换
        part.sm.send_to_outsource(
            outsource_company=company, process=process,
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)

        # 8. [2026-07-16] 把 APPROVED 报价 mark_used → USED + 写事件
        approved_quote.sm.mark_used(
            event_repo=self.quote_events, created_by=self._user_id,
        )
        approved_quote.updated_by = self._user_id
        await self.outsource_quotes.update(approved_quote)

        await self._broadcast()
        await self._check_parent_assembly(part)
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

        body 复用 PlaceOnShelfRequest（shelf_id + next_process_id）；
        额外校验：next_process_id 必须是 INHOUSE（外协回来后通常进车间）。
        """
        part = await self._get_part_or_404(part_id)
        await refresh_for_state_machine(
            self.parts.session,
            part,
            attrs=("status", "location", "next_process_id"),
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

        # 状态机转换：落到 ON_SHELF（on_enter_ON_SHELF 设置 shelf/process/holder/placed_at）
        part.sm.receive_from_outsource(
            shelf=shelf, process=process,
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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

        复用现有 pass_inspection 实现二次转换（同一事务连续两次状态机调用）。
        """
        part = await self._get_part_or_404(part_id)
        await refresh_for_state_machine(
            self.parts.session,
            part,
            attrs=("status", "location", "next_process_id"),
        )
        target_shelf = await self._validate_inspection_shelf(data.shelf_id)

        # 第一次转换：OUTSOURCE → INSPECTION
        part.sm.inspect_from_outsource(
            target_shelf=target_shelf,
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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
            return await self.pass_inspection(part.id)
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

        2026-07-10 起加文件前置校验（项目约定 9）：
        - 必须已上传 ≥1 G 代码（kind=G_CODE）；
        - 必须已上传 ≥1 CNC 设定单（kind=SETUP_SHEET）；
        - 否则 BIZ_CNC_PROGRAM_REQUIRED / BIZ_CNC_SETUP_SHEET_REQUIRED。
        """
        part = await self._get_part_or_404(part_id)
        # 1) 前置文件校验（DB 访问校验，按项目约定放 service 层）
        await self._assert_cnc_release_prerequisites(part_id)
        # 2) 货架 / 工序校验
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )
        # 3) 状态机转换
        part.sm.release_from_programming(
            shelf=shelf, process=process, event_repo=self.events,
            created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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

    async def _assert_cnc_release_prerequisites(self, part_id: int) -> None:
        """下发前置：必须已上传 ≥1 G 代码 + ≥1 CNC 设定单（否则拒绝）。"""
        from model.enums import PartFileKind

        if self.files is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing part file repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        g_codes = await self.files.list_by_part(part_id, kind=PartFileKind.G_CODE.value)
        if not g_codes:
            raise BizError(
                code=ErrCode.BIZ_CNC_PROGRAM_REQUIRED,
                message="未上传 G 代码，无法下发零件到货架",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        setup_sheets = await self.files.list_by_part(
            part_id, kind=PartFileKind.SETUP_SHEET.value,
        )
        if not setup_sheets:
            raise BizError(
                code=ErrCode.BIZ_CNC_SETUP_SHEET_REQUIRED,
                message="未上传 CNC 设定单，无法下发零件到货架",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

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

        if part.status != "IN_PROCESS" or part.location != "PRODUCTION_SHELF":
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=(
                    f"pick-up requires IN_PROCESS on PRODUCTION_SHELF; "
                    f"current status={part.status}, location={part.location}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if part.current_holder_id != shelf.id:
            raise BizError(
                code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                message=(
                    f"part is currently held by {part.current_holder_id}; "
                    f"expected this shelf {shelf.id}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # state machine handles holder switch + event creation
        part.sm.pick_up(
            worker=worker, shelf=shelf, event_repo=self.events,
            created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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

        from_status = _parse_status(part.status)
        event_type = _parse_event_type(data.event_type)

        if event_type == PartEventType.RETURNED:
            if part.status != "IN_PROCESS" or part.location != "WORKER":
                raise BizError(
                    code=ErrCode.BIZ_INVALID_TRANSITION,
                    message=(
                        f"return requires IN_PROCESS with WORKER location; "
                        f"current status={part.status}, location={part.location}"
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
            if part.current_holder_id != worker.id:
                raise BizError(
                    code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                    message=(
                        f"part is not held by worker {worker.id}; "
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
            if self.processes is not None and part.next_process_id is not None:
                prev = await self.processes.get_by_id(part.next_process_id)
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

            part.sm.return_to_shelf(
                worker=worker,
                shelf=shelf,
                process=new_process,
                prev_process_code=prev_process_code,
                worker_work_type_code=worker_work_type_code,
                event_repo=self.events,
                created_by=self._user_id,
            )
            part.updated_by = self._user_id
            await self.parts.update(part)
            await self._broadcast()
            await self._check_parent_assembly(part)
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
            target = await self.shelves.get_by_id(data.target_inspection_shelf_id)
            if target is None or target.deleted_at is not None:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_NOT_FOUND,
                    message=f"inspection shelf {data.target_inspection_shelf_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if target.zone != ShelfZone.INSPECTION.value:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"target shelf {target.code!r} is zone={target.zone!r}; "
                        "INSPECTED requires INSPECTION zone"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if not target.is_active:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_IN_USE,
                    message=f"inspection shelf {target.code!r} is inactive",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if part.current_holder_id != worker.id:
                raise BizError(
                    code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                    message="part is not held by current worker",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

            part.sm.inspect(
                worker=worker, target_shelf=target, event_repo=self.events,
                created_by=self._user_id,
            )
            part.updated_by = self._user_id
            await self.parts.update(part)
            await self._broadcast()
            await self._check_parent_assembly(part)
            items = await self._to_out([part])
            await self._broadcast_event(
                "INSPECTED",
                self._banner_payload(
                    part,
                    customer_path=items[0].customer_path,
                    worker_name=worker.name,
                    shelf_code=target.code,
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
        rows = await self.parts.list_for_work_type(
            shelf_id=shelf_id,
            mapped_process_ids=process_ids,
        )
        return await self._to_out(rows)

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
        rows = await self.parts.list_for_work_type_all_shelves(
            mapped_process_ids=process_ids,
            shelf_ids=shelf_ids,
        )
        return await self._to_out(rows)

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
        rows = await self.parts.list_held_by_worker(worker_id=worker_id)
        return await self._to_out(rows)

    async def pass_inspection(self, part_id: int) -> PartOut:
        """INSPECTION -> READY_TO_SHIP：品检合格。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        await refresh_for_state_machine(
            self.parts.session,
            part,
            attrs=("status", "location", "next_process_id"),
        )
        part.sm.pass_inspection(
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def deliver(
        self,
        part_id: int,
        *,
        actual_delivery_date: date | None = None,
        worker_badge_code: str | None = None,
    ) -> PartOut:
        """READY_TO_SHIP -> DELIVERED：发货。

        - 文员/管理员手动调用：worker_badge_code=None → 走原通用路径，
          不写 actual_delivery_date。
        - 扫码台司机调用：worker_badge_code 必填；service 层校验
          `t_worker.work_type.code == '送货司机'` 且 is_active；写入
          `part.actual_delivery_date = today()` 与 PartEvent.worker_id / badge_code。

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

        part.actual_delivery_date = actual_delivery_date or date.today()
        part.sm.deliver(
            worker=driver, event_repo=self.events,
            created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def complete(self, part_id: int) -> PartOut:
        """DELIVERED -> COMPLETED：确认完成，释放流水号。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.complete(
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def start_repair(self, part_id: int) -> PartOut:
        """-> REPAIRING：开始返修（从 INSPECTION / READY_TO_SHIP / DELIVERED 进入）。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.start_repair(
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def complete_repair(self, part_id: int, shelf_id: int) -> PartOut:
        """REPAIRING -> IN_PROCESS：返修完成，放回生产货架。

        2026-07-17：补 shelf↔process 校验——REPAIRING 期间 `next_process_id`
        由 start_repair 透传保留（ON_SHELF 进入时不传 process，next_process_id
        沿用 REPAIRING 之前）；如果 caller 选了不兼容的 shelf，422 拒绝。
        `next_process_id IS NULL`（fail_inspection 已清空）时跳过校验。
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
        if shelf.zone != ShelfZone.PRODUCTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"shelf {shelf.code!r} is zone={shelf.zone!r}; complete_repair requires PRODUCTION",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if part.next_process_id is not None:
            carried_process = await self._get_process(part.next_process_id)
            await self._assert_shelf_maps_process(shelf, carried_process)
        part.sm.complete_repair(
            shelf=shelf, event_repo=self.events,
            created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
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
        if part.status != PartStatus.INSPECTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=(
                    f"part {part_id} status={part.status!r}; "
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
        part.next_process_id = process.id  # 保留 inspector 指定的下一道工序
        part.sm.fail_inspection(
            shelf=shelf, process=process, event_repo=self.events,
            created_by=self._user_id,
            note=data.note,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def cancel(self, part_id: int) -> PartOut:
        """-> CANCELLED：取消零件，释放流水号。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.cancel(
            event_repo=self.events, created_by=self._user_id,
        )
        part.updated_by = self._user_id
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def _check_parent_assembly(self, part: TPart) -> None:
        """Part 状态变更后，检查并自动更新父 Assembly 状态。"""
        if part.assembly_id is None:
            return
        from model.assembly import TAssembly

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

        children = await self.parts.list_children(assembly.id)
        non_cancelled = [p for p in children if p.status != "CANCELLED"]

        # PENDING -> IN_PROCESS: any non-cancelled child is not PENDING
        if assembly.status == "PENDING" and any(
            p.status not in ("PENDING", "CANCELLED") for p in non_cancelled
        ):
            assembly.sm.start_production()
            assembly.updated_by = self._user_id
            await session.flush()

        # IN_PROCESS -> COMPLETED: all non-cancelled children are COMPLETED
        if assembly.status == "IN_PROCESS" and non_cancelled and all(
            p.status == "COMPLETED" for p in non_cancelled
        ):
            assembly.sm.complete()
            assembly.updated_by = self._user_id
            await session.flush()

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
        delivery_note_ids = {
            int(p.delivery_note_id)
            for p in rows
            if p.delivery_note_id
        }
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
                    delivery_note_id=p.delivery_note_id,
                    delivery_note_no=(
                        delivery_note_map.get(int(p.delivery_note_id))[0]
                        if p.delivery_note_id
                        and delivery_note_map.get(int(p.delivery_note_id))
                        else None
                    ),
                    delivery_note_status=(
                        delivery_note_map.get(int(p.delivery_note_id))[1]
                        if p.delivery_note_id
                        and delivery_note_map.get(int(p.delivery_note_id))
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
                )
            )
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
                )
            )
        return out
