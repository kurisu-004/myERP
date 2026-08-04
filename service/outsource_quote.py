"""外协报价 (OutsourceQuote) 业务逻辑层（2026-07-16 新增）。

设计要点：
- 跟 PartService / OutsourceCompanyService 形态对称：构造时注入仓库 + current_user；
  所有方法 `async`；service 负责业务校验 / 跨表查询 / 状态机触发；
  ORM UPDATE 由 SQLAlchemy OCC（version_id_col）自动处理并发冲突。
- 雪花 ID 入参一律 `str`（CLAUDE.md §3），service 端 parse_snowflake_id 转 int。
- 乐观锁：update / approve / reject 三处用 request.version 跟 model.version 比对；
  不一致 → `BIZ_VERSION_CONFLICT 409`。
- 状态机返回的 transition 由 service 触发，回调内会写 `TOutsourceQuoteEvent`
  （经 `quote_events.add`，不在此 flush —— 跟随调用方的 session lifecycle）。
"""
from __future__ import annotations

from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from core.time import now_naive
from model import TOutsourceQuote, TPart, TPartBatch
from model.customer import TCustomer
from model.enums import (
    OutsourceQuoteEventType,
    OutsourceQuoteSortKey,
    OutsourceQuoteStatus,
    PartEventType,
    PartStatus,
    ProcessCategory,
    SortDir,
)
from model.outsource_quote_event import TOutsourceQuoteEvent
from model.part_event import TPartEvent
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.outsource_shipment import OutsourceShipmentRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.worker import WorkerRepository
from schema.outsource_quote import (
    ApprovedForSendListOut,
    ApprovedQuoteForSendItem,
    OutsourceQuoteApproveRequest,
    OutsourceQuoteCreateRequest,
    OutsourceQuoteListOut,
    OutsourceQuoteListQuery,
    OutsourceQuoteOut,
    OutsourceQuoteRejectRequest,
    OutsourceQuoteUpdateRequest,
    OutsourceShipmentOut,
    OutsourceShipmentReconcileUpdateRequest,
    OutsourceInFlightItem,
)
from schema.part import PartListItem
from service._id_parse import parse_snowflake_id
from service._session_refresh import refresh_for_state_machine
from service._customer_helpers import (  # 2026-07-28：抽到共享模块
    expand_customer_ids,
    make_customer_path_cached,
    preload_customer_cache,
)
from utils.id_gen import new_id


class OutsourceQuoteService:
    """外协报价单业务逻辑层（2026-07-30 扩展：兼管 shipment 对账 + in-flight）。"""

    def __init__(
        self,
        quotes: OutsourceQuoteRepository,
        quote_events: OutsourceQuoteEventRepository,
        parts: PartRepository,
        companies: OutsourceCompanyRepository,
        processes: ProcessRepository,
        customers: CustomerRepository,
        shelves: ShelfRepository,
        workers: WorkerRepository,
        *,
        part_events: PartEventRepository,
        shipments: OutsourceShipmentRepository | None = None,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.quotes = quotes
        self.quote_events = quote_events
        self.parts = parts
        self.companies = companies
        self.processes = processes
        self.customers = customers
        # PR-H 2026-07-28：picker 端点要组装 PartListItem（含 shelf_code / worker_name）
        self.shelves = shelves
        self.workers = workers
        self.part_events = part_events
        self.shipments = shipments
        self._user_id: int | None = current_user.id if current_user else None

    # ============================================================
    # 查询
    # ============================================================

    async def list_quotes(
        self, query: OutsourceQuoteListQuery,
    ) -> OutsourceQuoteListOut:
        """报价一览列表。"""
        part_filter_ids = await self._resolve_part_ids(
            customer_id=query.customer_id,
            keyword=query.keyword,
        )
        if part_filter_ids is not None and not part_filter_ids:
            return OutsourceQuoteListOut(
                items=[], total=0,
                limit=query.limit, offset=query.offset,
            )

        rows, total = await self._search_quotes(
            status=query.status.value if query.status else None,
            statuses=[s.value for s in query.statuses] if query.statuses else None,
            part_id=parse_snowflake_id(query.part_id, field_name="part_id") if query.part_id else None,
            outsource_company_id=(
                parse_snowflake_id(query.outsource_company_id, field_name="outsource_company_id")
                if query.outsource_company_id else None
            ),
            part_filter_ids=part_filter_ids,
            sort_by=query.sort_by, sort_dir=query.sort_dir,
            limit=query.limit, offset=query.offset,
        )
        return OutsourceQuoteListOut(
            items=await self._to_out_many(rows),
            total=total,
            limit=query.limit, offset=query.offset,
        )

    async def get_quote(self, quote_id: str) -> OutsourceQuoteOut:
        qid = parse_snowflake_id(quote_id, field_name="quote_id")
        if qid is None:
            raise self._not_found(quote_id)
        quote = await self.quotes.get_by_id(qid)
        if quote is None:
            raise self._not_found(quote_id)
        return await self._to_out(quote)

    # ============================================================
    # 新建报价 picker 默认筛选（PR-H 2026-07-28）
    # ============================================================
    async def list_quotable_parts_for_picker(
        self,
        *,
        keyword: str | None,
        limit: int,
        shelf_processes: "ShelfProcessRepository",
    ) -> list[PartListItem]:
        """新建外协报价对话框 picker 默认数据源（2026-07-29 批次化，行=批次）。

        谓词（PR-fix-0.2.0 hotfix：谓词走 TPartBatch，不再读 TPart rollup 字段）：
          - TPartBatch.status='IN_PROCESS' AND TPartBatch.location='PRODUCTION_SHELF'
          - AND TPartBatch.current_holder_id ∈ (绑定了 OUTSOURCE 工序的货架 id 集合)

        返回 PartListItem 列表，包含：
          - next_process_id / next_process_name（前端自动填工序）
          - batch_id / batch_no / batch_quantity（picker 选中时传给报价，
            与 picker 行对应的那条批次；多批次工单下避免整张工单报价）

        若系统无任何绑定了 OUTSOURCE 工序的货架，返回空列表（前端 picker 提示空）。
        """
        shelf_ids = await shelf_processes.list_shelf_ids_with_process_category(
            ProcessCategory.OUTSOURCE.value,
        )
        if not shelf_ids:
            return []
        # 2026-07-29 PR-fix-0.2.0 hotfix：rows 是 list[tuple[TPartBatch, TPart]]；
        # _to_part_list_items 的 contract 现在严格只接受这种 tuple 形状（不再鸭子
        # 类型兼容 list[TPart]）。修的是上一轮 PR 漏掉的一处：`rows` 元素是 tuple，
        # 但 `_to_part_list_items` 在批查时直接 `for p in rows: p.customer_id`
        # 把 tuple 当 TPart 用 → 500。
        rows = await self.parts.list_quotable_for_outsource_quote(
            keyword=keyword, shelf_ids_in=shelf_ids, limit=limit,
        )
        return await self._to_part_list_items(rows)

    async def _to_part_list_items(
        self, rows: list[tuple[TPartBatch, TPart]],
    ) -> list[PartListItem]:
        """批次化 picker 的字段组装器：行 = (TPartBatch, TPart)，把 TPart 字段映射到
        PartListItem，并把批次字段（batch_id / batch_no / batch_quantity）一并填上。

        简化版：只填 Picker 关心的字段（不依赖 shelves/workers/shelf_processes），
        但需要 customer_name / parent_customer_name / customer_path / shelf_code /
        worker_name / next_process_name 等展示字段。

        Contract（2026-07-29 PR-fix-0.2.0 hotfix）：
        - rows 必须是 list[tuple[TPartBatch, TPart]]（批次化 repo 返回形状）。
        - 工单级 location / current_holder_id 已 rollup 到 TPart 字段（与批次行
          视图保持一致），但 picker 关心的展示字段（status / serial_no 等）从
          TPart 取就够了。

        重构背景：早期实现接受 list[TPart]（工单级）+ 内部鸭子类型解包 list[tuple]
        + 一个迟到的 normalized 列表，结果在 4 个批查（customer / worker / shelf /
        process）那里就先用 `for p in rows` 访问 .customer_id，rows 还是 tuple 时直接
        'tuple' object has no attribute 'customer_id' 500。锁住单一 contract 让回归
        测试一次性拍死此类 bug。
        """
        if not rows:
            return []

        # 立刻解包：所有下游访问都从 parts 列表（去 tuple 后）取，避免再误用 tuple 字段
        parts: list[TPart] = [p for _, p in rows]

        # 1. 客户批查（含 parent）
        cust_ids = list({p.customer_id for p in parts})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map: dict[int, TCustomer] = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = (
            await self.customers.list_by_ids(parent_ids) if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {p.id: p for p in parents}

        # 2. 工人批查（WORKER 持有）
        worker_ids = [
            int(p.current_holder_id)
            for p in parts
            if p.location == "WORKER" and p.current_holder_id
        ]
        worker_map: dict[int, str] = {}
        if worker_ids:
            worker_rows = await self.workers.list_by_ids(worker_ids)
            worker_map = {w.id: w.name for w in worker_rows}

        # 3. 货架批查（PRODUCTION_SHELF / INSPECTION_SHELF 持有）
        shelf_ids = [
            int(p.current_holder_id)
            for p in parts
            if p.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF")
            and p.current_holder_id
        ]
        shelf_map: dict[int, str] = {}
        if shelf_ids:
            shelf_rows = await self.shelves.list_by_ids(shelf_ids)
            shelf_map = {s.id: s.code for s in shelf_rows}

        # 4. 下一工序批查
        next_process_ids = list({
            int(p.next_process_id) for p in parts if p.next_process_id
        })
        process_map: dict[int, str] = {}
        if next_process_ids:
            proc_rows = await self.processes.list_by_ids(next_process_ids)
            process_map = {pr.id: pr.name for pr in proc_rows}

        out: list[PartListItem] = []
        for batch, p in rows:
            cust = cust_map.get(p.customer_id)
            parent = (
                parent_map.get(cust.parent_id)
                if cust and cust.parent_id else None
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
                    status=PartStatus(p.status) if p.status else PartStatus.PENDING,
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
                    next_process_id=p.next_process_id,
                    next_process_name=(
                        process_map.get(int(p.next_process_id))
                        if p.next_process_id else None
                    ),
                    # 2026-07-29 PR-fix-0.2.0：批次化字段
                    # 行=批次：把 batch 字段填进 PartListItem，picker 选中时回传给报价。
                    batch_id=batch.id,
                    batch_no=batch.batch_no,
                    batch_quantity=batch.quantity,
                )
            )
        return out

    # ============================================================
    # 写
    # ============================================================

    async def create_quote(
        self, data: OutsourceQuoteCreateRequest,
    ) -> OutsourceQuoteOut:
        part_id = parse_snowflake_id(data.part_id, field_name="part_id")
        company_id = parse_snowflake_id(
            data.outsource_company_id, field_name="outsource_company_id",
        )
        process_id = parse_snowflake_id(data.process_id, field_name="process_id")
        if part_id is None or company_id is None or process_id is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="part_id / outsource_company_id / process_id 必须是合法的雪花 ID",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        company = await self.companies.get_by_id(company_id)
        if company is None:
            raise self._not_found(data.outsource_company_id)
        process = await self.processes.get_by_id(process_id)
        if process is None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {process_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if process.category != ProcessCategory.OUTSOURCE.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS,
                message=f"工序「{process.code}」不是 OUTSOURCE 类别，不能创建外协报价",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 重复检查（应用层预校验 + DB 部分唯一索引双重兜底）
        existing = await self.quotes.get_one_active_for_tuple(
            part_id=part_id,
            outsource_company_id=company_id,
            process_id=process_id,
        )
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_DUPLICATE,
                message=(
                    f"同一 (零件 {part.serial_no!r} / 外协公司「{company.name}」 / 工序「{process.code}」)"
                    f"已存在活跃报价 #{existing.id}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )

        quote = TOutsourceQuote(
            id=new_id(),
            part_id=part_id,
            outsource_company_id=company_id,
            process_id=process_id,
            price=data.price,
            note=(data.note or None),
            status=OutsourceQuoteStatus.DRAFT.value,
        )
        quote.created_by = self._user_id
        quote.updated_by = self._user_id
        try:
            await self.quotes.create(quote)
        except IntegrityError as e:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_DUPLICATE,
                message=(
                    f"同一 (零件 {part_id} / 外协公司 {company_id} / 工序 {process_id})"
                    "已存在活跃报价"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            ) from e

        # 初始 CREATED 事件
        await self.quote_events.create(TOutsourceQuoteEvent(
            id=new_id(),
            quote_id=quote.id,
            event_type="CREATED",
            from_status=None,
            to_status=OutsourceQuoteStatus.DRAFT.value,
            created_by=self._user_id,
        ))

        # 同步写一行 TPartEvent（2026-07-16）：让 PartDetail 历史时间线
        # 看到「谁什么时候为此零件创建了外协报价」。
        # note 是中文模板，包含外协公司 / 工序 / 报价 id 便于人工追溯。
        await self.part_events.create(TPartEvent(
            id=new_id(),
            part_id=part_id,
            event_type=PartEventType.QUOTE_CREATED.value,
            from_status=None,
            to_status=None,
            note=(
                f"外协公司:{company.name} "
                f"工序:{process.code} "
                f"报价:#{quote.id}"
            ),
            created_by=self._user_id,
        ))

        return await self._to_out(quote)

    async def update_quote(
        self, quote_id: str, data: OutsourceQuoteUpdateRequest,
    ) -> OutsourceQuoteOut:
        qid = parse_snowflake_id(quote_id, field_name="quote_id")
        if qid is None:
            raise self._not_found(quote_id)
        quote = await self.quotes.get_by_id(qid)
        if quote is None:
            raise self._not_found(quote_id)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("status", "version"),
        )
        if quote.status != OutsourceQuoteStatus.DRAFT.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION,
                message="只有 DRAFT 状态的报价可以修改，请先撤回或新建",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        # 乐观锁
        if quote.version != data.version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message=(
                    f"报价版本不一致：当前 DB version={quote.version}，"
                    f"请求携带 version={data.version}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )

        if data.price is not None:
            quote.price = data.price
        if data.note is not None:
            quote.note = data.note or None

        quote.updated_by = self._user_id
        await self.quotes.update(quote)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("updated_at",),
        )

        await self.quote_events.create(TOutsourceQuoteEvent(
            id=new_id(),
            quote_id=quote.id,
            event_type="EDITED",
            from_status=quote.status,
            to_status=quote.status,
            created_by=self._user_id,
        ))
        return await self._to_out(quote)

    async def submit_quote(self, quote_id: str) -> OutsourceQuoteOut:
        """DRAFT → SUBMITTED。"""
        qid = parse_snowflake_id(quote_id, field_name="quote_id")
        if qid is None:
            raise self._not_found(quote_id)
        quote = await self.quotes.get_by_id(qid)
        if quote is None:
            raise self._not_found(quote_id)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("status", "version"),
        )
        if quote.status != OutsourceQuoteStatus.DRAFT.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION,
                message=f"当前状态 {quote.status} 不允许 submit",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        quote.sm.submit(event_repo=self.quote_events, created_by=self._user_id)
        quote.updated_by = self._user_id
        await self.quotes.update(quote)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("updated_at",),
        )
        return await self._to_out(quote)

    async def approve_quote(
        self, quote_id: str, data: OutsourceQuoteApproveRequest,
    ) -> OutsourceQuoteOut:
        """SUBMITTED → APPROVED（MANAGER-only，端点层 enforce）。

        2026-07-30：批准前先把同 (part_id, process_id) 的其他 SUBMITTED/APPROVED
        报价（含 DIRECT 占位）全部置 REJECTED，保证同工序只有一条生效报价。
        """
        qid = parse_snowflake_id(quote_id, field_name="quote_id")
        if qid is None:
            raise self._not_found(quote_id)
        quote = await self.quotes.get_by_id(qid)
        if quote is None:
            raise self._not_found(quote_id)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("status", "version"),
        )
        if quote.status != OutsourceQuoteStatus.SUBMITTED.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION,
                message=f"当前状态 {quote.status} 不允许 approve",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if quote.version != data.version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message="报价版本不一致，请刷新后重试",
                http_status=http_status.HTTP_409_CONFLICT,
            )

        # 2026-07-30：自动拒绝同 (part, process) 的其他活跃报价
        competitors = await self.quotes.list_active_by_part_process(
            part_id=quote.part_id,
            process_id=quote.process_id,
            exclude_id=quote.id,
        )
        for competitor in competitors:
            competitor.status = OutsourceQuoteStatus.REJECTED.value
            competitor.reviewed_at = now_naive()
            competitor.review_note = "被新批准报价取代"
            competitor.updated_by = self._user_id
            await self.quotes.update(competitor)
            await self.quote_events.create(TOutsourceQuoteEvent(
                id=new_id(),
                quote_id=competitor.id,
                event_type=OutsourceQuoteEventType.REJECTED.value,
                from_status=competitor.status,
                to_status=OutsourceQuoteStatus.REJECTED.value,
                note="被新批准报价取代",
                created_by=self._user_id,
            ))

        quote.sm.approve(
            review_note=data.review_note,
            event_repo=self.quote_events,
            created_by=self._user_id,
        )
        quote.updated_by = self._user_id
        await self.quotes.update(quote)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("updated_at",),
        )

        # 同步写一行 TPartEvent（2026-07-16）：让 PartDetail 历史时间线
        # 看到「谁什么时候通过了这个零件的外协报价」。
        # 报销金额信息不入 note（隐私），只记录公司/工序/报价 id/审批意见。
        company = await self.companies.get_by_id(quote.outsource_company_id)
        process = await self.processes.get_by_id(quote.process_id)
        await self.part_events.create(TPartEvent(
            id=new_id(),
            part_id=quote.part_id,
            event_type=PartEventType.QUOTE_APPROVED.value,
            from_status=None,
            to_status=None,
            note=(
                f"外协公司:{company.name if company else quote.outsource_company_id} "
                f"工序:{process.code if process else quote.process_id} "
                f"报价:#{quote.id} "
                f"审批意见:{data.review_note or '无'}"
            ),
            created_by=self._user_id,
        ))

        return await self._to_out(quote)

    async def reject_quote(
        self, quote_id: str, data: OutsourceQuoteRejectRequest,
    ) -> OutsourceQuoteOut:
        """SUBMITTED → REJECTED（review_note 必填，MANAGER-only，端点层 enforce）。"""
        qid = parse_snowflake_id(quote_id, field_name="quote_id")
        if qid is None:
            raise self._not_found(quote_id)
        quote = await self.quotes.get_by_id(qid)
        if quote is None:
            raise self._not_found(quote_id)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("status", "version"),
        )
        if quote.status != OutsourceQuoteStatus.SUBMITTED.value:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION,
                message=f"当前状态 {quote.status} 不允许 reject",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if quote.version != data.version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message="报价版本不一致，请刷新后重试",
                http_status=http_status.HTTP_409_CONFLICT,
            )

        quote.sm.reject(
            review_note=data.review_note,
            event_repo=self.quote_events,
            created_by=self._user_id,
        )
        quote.updated_by = self._user_id
        await self.quotes.update(quote)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("updated_at",),
        )
        return await self._to_out(quote)

    async def soft_delete_quote(self, quote_id: str) -> None:
        """软删（仅 DRAFT / REJECTED 可删）。"""
        qid = parse_snowflake_id(quote_id, field_name="quote_id")
        if qid is None:
            raise self._not_found(quote_id)
        quote = await self.quotes.get_by_id(qid)
        if quote is None:
            raise self._not_found(quote_id)
        await refresh_for_state_machine(
            self.quotes.session, quote, attrs=("status",),
        )
        if quote.status not in (
            OutsourceQuoteStatus.DRAFT.value,
            OutsourceQuoteStatus.REJECTED.value,
        ):
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION,
                message=(
                    f"已提交 / 已批准 / 已使用的报价不可删除；当前状态 {quote.status}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        quote.updated_by = self._user_id
        await self.quotes.soft_delete(quote)

    # ============================================================
    # 对账页更新（2026-07-30）：迁移到 t_outsource_shipment
    # ============================================================
    async def reconcile_update_shipment(
        self, shipment_id: str, data: OutsourceShipmentReconcileUpdateRequest,
    ) -> OutsourceShipmentOut:
        """对账页双击编辑 shipment：单价 / 数量 / 对账标记。

        允许状态 OUTSOURCING / RECEIVED。
        is_billed 为纯标志位，不驱动状态机。
        """
        if self.shipments is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing shipment repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        sid = parse_snowflake_id(shipment_id, field_name="shipment_id")
        if sid is None:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_SHIPMENT_NOT_FOUND,
                message=f"shipment {shipment_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        shipment = await self.shipments.get_by_id(sid)
        if shipment is None:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_SHIPMENT_NOT_FOUND,
                message=f"shipment {shipment_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        await refresh_for_state_machine(
            self.shipments.session, shipment, attrs=("status", "version"),
        )
        if shipment.version != data.version:
            raise BizError(
                code=ErrCode.BIZ_VERSION_CONFLICT,
                message="该发货记录已被其他用户修改，请刷新后重试",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        if shipment.status not in ("OUTSOURCING", "RECEIVED"):
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION,
                message=(
                    f"对账编辑仅允许 OUTSOURCING / RECEIVED 状态；"
                    f"当前 {shipment.status}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        if data.unit_price is not None:
            shipment.unit_price = data.unit_price
        if data.quantity is not None:
            shipment.quantity = data.quantity
        if data.is_billed is not None:
            shipment.is_billed = bool(data.is_billed)

        shipment.updated_by = self._user_id
        await self.shipments.update(shipment)
        await refresh_for_state_machine(
            self.shipments.session, shipment, attrs=("updated_at",),
        )
        return await self._to_shipment_out(shipment)

    # ============================================================
    # in-flight 外协中批次列表（2026-07-30 新增）
    # ============================================================
    async def list_in_flight(
        self,
        *,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[OutsourceInFlightItem], int]:
        """返回外协中批次列表（行=批次 + 左联开口 shipment）。"""
        if self.shipments is None:
            return [], 0
        total = await self.shipments.count_in_flight(keyword=keyword)
        if total == 0:
            return [], 0
        rows = await self.shipments.list_in_flight(
            keyword=keyword, limit=limit, offset=offset,
        )

        # 批查：公司名、工序名、客户路径
        company_ids = [
            int(s.outsource_company_id)
            for s, _, _ in rows if s is not None and s.outsource_company_id
        ]
        company_map: dict[int, str] = {}
        if company_ids:
            company_rows = await self.companies.list_by_ids(list(set(company_ids)))
            company_map = {c.id: c.name for c in company_rows}

        process_ids = [
            int(b.next_process_id)
            for _, _, b in rows if b is not None and b.next_process_id
        ]
        process_map: dict[int, str] = {}
        if process_ids:
            proc_rows = await self.processes.list_by_ids(list(set(process_ids)))
            process_map = {p.id: p.name for p in proc_rows}

        part_ids = [p.id for _, p, _ in rows]
        cust_cache = await preload_customer_cache(
            self.customers,
            [p.customer_id for _, p, _ in rows if p.customer_id],
        ) if self.customers else {}

        items: list[OutsourceInFlightItem] = []
        for shipment, part, batch in rows:
            customer_path: str | None = None
            if part.customer_id and part.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[part.customer_id], cust_cache,
                )
            next_process_id = batch.next_process_id if batch else None
            items.append(OutsourceInFlightItem(
                part_id=part.id,
                batch_id=batch.id if batch else None,
                batch_no=batch.batch_no if batch else None,
                quantity=batch.quantity if batch else None,
                serial_no=part.serial_no,
                drawing_no=part.drawing_no,
                name=part.name,
                # 2026-08-04 新增：part 已是 rows tuple 里的 TPart，直接读列
                is_urgent=bool(part.is_urgent),
                customer_path=customer_path,
                next_process_id=next_process_id,
                next_process_name=process_map.get(int(next_process_id)) if next_process_id else None,
                outsource_company_id=shipment.outsource_company_id if shipment else None,
                outsource_company_name=company_map.get(int(shipment.outsource_company_id)) if shipment and shipment.outsource_company_id else None,
                sent_at=shipment.sent_at if shipment else None,
                version=batch.version if batch else 0,
            ))
        return items, total

    # ============================================================
    # 「外协发送」列表：至少有一条 APPROVED 报价 + 状态可发送的零件
    # ============================================================

    async def list_approved_for_send(
        self,
        *,
        keyword: str | None = None,
        customer_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ApprovedForSendListOut:
        # 1. 全部 APPROVED 报价（一次查询）；按 part 取「第一条」（list_all_approved
        #    已按 part_id ASC, created_at DESC, id DESC 排序 → 每 part 取最新创建）
        all_quotes = await self.quotes.list_all_approved()
        if not all_quotes:
            return ApprovedForSendListOut(
                items=[], total=0, limit=limit, offset=offset,
            )
        quotes_by_part: dict[int, TOutsourceQuote] = {}
        for q in all_quotes:
            if q.part_id not in quotes_by_part:
                quotes_by_part[q.part_id] = q
        approved_part_ids = list(quotes_by_part.keys())

        # 2. customer 过滤展开
        customer_ids_in: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is None:
                return ApprovedForSendListOut(
                    items=[], total=0, limit=limit, offset=offset,
                )
            customer_ids_in = await expand_customer_ids(self.customers, cid_int)

        kw = keyword.strip() if keyword else None
        if not kw:
            kw = None

        # 3. 「可发送」资格 + 过滤 + 分页全部下沉到 SQL（有 APPROVED 报价 ∩ 可发送状态）
        total = await self.parts.count_outsource_sendable(
            part_ids_in=approved_part_ids,
            customer_ids_in=customer_ids_in,
            keyword=kw,
        )
        if total == 0:
            return ApprovedForSendListOut(
                items=[], total=0, limit=limit, offset=offset,
            )
        # 2026-07-29 批次化：行=批次（list_outsource_sendable 返回 list[tuple[TPartBatch, TPart]]）
        page_rows = await self.parts.list_outsource_sendable(
            part_ids_in=approved_part_ids,
            customer_ids_in=customer_ids_in,
            keyword=kw,
            limit=limit, offset=offset,
        )

        # 4. 批查 process（批次 next_process + 报价 process）/ company / customer
        # 2026-07-29：page_rows 是 list[tuple[TPartBatch, TPart]]
        need_proc_ids: set[int] = set()
        for batch, p in page_rows:
            if batch.next_process_id is not None:
                need_proc_ids.add(batch.next_process_id)
            q = quotes_by_part.get(p.id)
            if q is not None:
                need_proc_ids.add(q.process_id)
        proc_map = {
            pr.id: pr
            for pr in (
                await self.processes.list_by_ids(list(need_proc_ids))
                if need_proc_ids else []
            )
        }
        company_ids = {
            quotes_by_part[p.id].outsource_company_id
            for _, p in page_rows if p.id in quotes_by_part
        }
        company_map = {
            c.id: c
            for c in (
                await self.companies.list_by_ids(list(company_ids))
                if company_ids else []
            )
        }
        cust_cache = await preload_customer_cache(self.customers,
            [p.customer_id for _, p in page_rows if p.customer_id is not None]
        )

        # 4b. 批查货架 code（PR-H 2026-07-28：外协发送一览显示源货架；2026-07-29 改用批次 holder）
        shelf_holder_ids = [
            int(batch.current_holder_id)
            for batch, _ in page_rows
            if batch.current_holder_id is not None
            and batch.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF")
        ]
        shelf_code_map: dict[int, str] = {}
        if shelf_holder_ids:
            shelf_rows = await self.shelves.list_by_ids(list(set(shelf_holder_ids)))
            shelf_code_map = {s.id: s.code for s in shelf_rows}

        # 5. 同步拼装 ApprovedQuoteForSendItem（无 await 在循环里；2026-07-29 批次化）
        items: list[ApprovedQuoteForSendItem] = []
        for batch, p in page_rows:
            q = quotes_by_part.get(p.id)
            if q is None:
                continue
            next_proc = (
                proc_map.get(batch.next_process_id)
                if batch.next_process_id is not None else None
            )
            company = company_map.get(q.outsource_company_id)
            process = proc_map.get(q.process_id)
            customer_path: str | None = None
            if p.customer_id is not None and p.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[p.customer_id], cust_cache,
                )
            items.append(ApprovedQuoteForSendItem(
                version=batch.version,  # 2026-07-29：OCC 在批次上
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
                # PR-H 2026-07-28：源货架 code（绑了外协工序的货架）
                shelf_code=(
                    shelf_code_map.get(int(batch.current_holder_id))
                    if batch.current_holder_id else None
                ),
                outsource_company_id=q.outsource_company_id,
                outsource_company_name=company.name if company else None,
                process_id=q.process_id,
                process_name=process.name if process else None,
                price=q.price,
            ))

        return ApprovedForSendListOut(
            items=items, total=total, limit=limit, offset=offset,
        )

    # ============================================================
    # 内部
    # ============================================================

    def _not_found(self, quote_id: str | int) -> BizError:
        return BizError(
            code=ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_FOUND,
            message=f"outsource quote {quote_id!r} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )

    async def _resolve_part_ids(
        self,
        *,
        customer_id: str | None,
        keyword: str | None,
    ) -> list[int] | None:
        """根据 customer_id / keyword 算出真实 part_id 集合；None 表示不限。

        customer + keyword 一起作为 PartRepository 过滤条件（同一次查询），
        返回的是真正的 t_part.id（历史 bug：曾把 customer_id 当 part_id 用）。
        无 500 截断（先 count 再按 count 取全量 id）。
        """
        customer_ids_in: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is None:
                return []
            customer_ids_in = await expand_customer_ids(self.customers, cid_int)

        kw = keyword.strip() if keyword else None
        if not kw:
            kw = None

        if customer_ids_in is None and kw is None:
            return None

        total = await self.parts.count_with_filters(
            customer_ids_in=customer_ids_in, keyword=kw,
        )
        if total == 0:
            return []
        rows = await self.parts.list_with_filters(
            customer_ids_in=customer_ids_in, keyword=kw,
            limit=total, offset=0,
        )
        return [p.id for p in rows]

    async def _search_quotes(
        self,
        *,
        status: str | None,
        statuses: list[str] | None = None,
        part_id: int | None,
        outsource_company_id: int | None,
        part_filter_ids: list[int] | None,
        sort_by: OutsourceQuoteSortKey,
        sort_dir: SortDir,
        limit: int,
        offset: int,
    ) -> tuple[list[TOutsourceQuote], int]:
        """一次 list + 一次 count（part_ids_in 收敛到匹配零件；排序/分页全在 SQL）。"""
        rows = await self.quotes.list_with_filters(
            status=status,
            statuses=statuses,
            part_id=part_id,
            part_ids_in=part_filter_ids,
            outsource_company_id=outsource_company_id,
            sort_by=sort_by, sort_dir=sort_dir,
            limit=limit, offset=offset,
        )
        total = await self.quotes.count_with_filters(
            status=status,
            statuses=statuses,
            part_id=part_id,
            part_ids_in=part_filter_ids,
            outsource_company_id=outsource_company_id,
        )
        return rows, total

    async def _to_out(self, q: TOutsourceQuote) -> OutsourceQuoteOut:
        """单条序列化（详情 / 写响应用）——薄包装 _to_out_many，避免两套逻辑。"""
        items = await self._to_out_many([q])
        return items[0]

    async def _to_out_many(
        self, quotes: list[TOutsourceQuote],
    ) -> list[OutsourceQuoteOut]:
        """批量序列化：part / company / process / customer 各一次批查，
        循环内无 await —— 把报价一览的 N+1（每行 3~5 次 get_by_id）压成常数条查询。
        """
        if not quotes:
            return []
        part_ids = list({q.part_id for q in quotes})
        company_ids = list({q.outsource_company_id for q in quotes})
        process_ids = list({q.process_id for q in quotes})

        part_map = {p.id: p for p in await self.parts.list_by_ids(part_ids)}
        company_map = {
            c.id: c for c in await self.companies.list_by_ids(company_ids)
        }
        proc_map = {
            pr.id: pr for pr in await self.processes.list_by_ids(process_ids)
        }
        cust_cache = await preload_customer_cache(self.customers,
            [p.customer_id for p in part_map.values() if p.customer_id]
        )

        out: list[OutsourceQuoteOut] = []
        for q in quotes:
            part = part_map.get(q.part_id)
            company = company_map.get(q.outsource_company_id)
            process = proc_map.get(q.process_id)
            customer_path: str | None = None
            if part and part.customer_id and part.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[part.customer_id], cust_cache,
                )
            out.append(OutsourceQuoteOut(
                id=q.id,
                version=q.version,
                part_id=q.part_id,
                outsource_company_id=q.outsource_company_id,
                process_id=q.process_id,
                price=q.price,
                note=q.note,
                status=q.status,
                submitted_at=q.submitted_at,
                reviewed_at=q.reviewed_at,
                review_note=q.review_note,
                # PR-H 2026-07-29：外协全生命周期字段
                sent_at=q.sent_at,
                received_at=q.received_at,
                quantity=q.quantity,
                is_billed=bool(getattr(q, "is_billed", False)),
                is_direct=bool(getattr(q, "is_direct", False)),
                created_at=q.created_at,
                updated_at=q.updated_at,
                part_serial_no=part.serial_no if part else None,
                part_drawing_no=part.drawing_no if part else None,
                part_name=part.name if part else None,
                outsource_company_name=company.name if company else None,
                process_code=process.code if process else None,
                process_name=process.name if process else None,
                customer_path=customer_path,
                # 2026-08-02 新增：所属零件的客户下单单价（part_map 已批量取，零额外查询）
                part_unit_price=part.unit_price if part else None,
                # 2026-08-04 新增：零件加急（part_map 已批量取，直接读 TPart.is_urgent）
                is_urgent=bool(part.is_urgent) if part else False,
            ))
        return out

    async def _to_shipment_out(
        self, s: "TOutsourceShipment",
    ) -> OutsourceShipmentOut:
        """单条 shipment 序列化。"""
        part = await self.parts.get_by_id(s.part_id)
        company = await self.companies.get_by_id(s.outsource_company_id)
        process = await self.processes.get_by_id(s.process_id)
        customer_path: str | None = None
        if part and part.customer_id:
            cust_cache = await preload_customer_cache(
                self.customers, [part.customer_id],
            )
            if part.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[part.customer_id], cust_cache,
                )
        batch_no: int | None = None
        if s.batch_id is not None and self.parts.session is not None:
            from model import TPartBatch
            batch = await self.parts.session.get(TPartBatch, s.batch_id)
            if batch is not None:
                batch_no = batch.batch_no
        return OutsourceShipmentOut(
            id=s.id,
            version=s.version,
            quote_id=s.quote_id,
            part_id=s.part_id,
            batch_id=s.batch_id,
            batch_no=batch_no,
            outsource_company_id=s.outsource_company_id,
            process_id=s.process_id,
            quantity=s.quantity,
            unit_price=s.unit_price,
            status=s.status,
            sent_at=s.sent_at,
            received_at=s.received_at,
            is_billed=bool(getattr(s, "is_billed", False)),
            created_at=s.created_at,
            updated_at=s.updated_at,
            part_drawing_no=part.drawing_no if part else None,
            part_name=part.name if part else None,
            outsource_company_name=company.name if company else None,
            process_name=process.name if process else None,
            customer_path=customer_path,
        )
