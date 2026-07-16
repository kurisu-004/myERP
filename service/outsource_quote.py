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
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TOutsourceQuote
from model.customer import TCustomer
from model.enums import (
    OutsourceQuoteSortKey,
    OutsourceQuoteStatus,
    PartEventType,
    ProcessCategory,
    SortDir,
)
from model.outsource_quote_event import TOutsourceQuoteEvent
from model.part_event import TPartEvent
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
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
)
from service._id_parse import parse_snowflake_id
from service._session_refresh import refresh_for_state_machine
from utils.id_gen import new_id


class OutsourceQuoteService:
    """外协报价单业务逻辑层。"""

    def __init__(
        self,
        quotes: OutsourceQuoteRepository,
        quote_events: OutsourceQuoteEventRepository,
        parts: PartRepository,
        companies: OutsourceCompanyRepository,
        processes: ProcessRepository,
        customers: CustomerRepository,
        *,
        part_events: PartEventRepository,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.quotes = quotes
        self.quote_events = quote_events
        self.parts = parts
        self.companies = companies
        self.processes = processes
        self.customers = customers
        self.part_events = part_events
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
            items=[await self._to_out(q) for q in rows],
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
        """SUBMITTED → APPROVED（MANAGER-only，端点层 enforce）。"""
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
        # 1. 全部 APPROVED 报价
        all_quotes = await self._list_all_approved_quotes()
        part_ids = sorted({q.part_id for q in all_quotes})
        if not part_ids:
            return ApprovedForSendListOut(
                items=[], total=0, limit=limit, offset=offset,
            )

        # 2. 取这些零件（PartRepository 没有 list_by_ids，借 list_with_filters 二次过滤）
        #    → 用 t_part.id IN(part_ids) 过滤；为简化走 keyword / customer 二次过滤
        all_parts = await self.parts.list_with_filters(
            customer_id=None,
            customer_ids_in=None,
            statuses=None,
            is_urgent=None,
            keyword=None,
            include_deleted=False,
            limit=500,
            offset=0,
        )
        all_parts = [p for p in all_parts if p.id in set(part_ids)]

        # 3. 「可发送」资格：PENDING 或 (IN_PROCESS + PRODUCTION_SHELF + OUTSOURCE next)
        #    同时记录零件的「下一道工序」名（用于响应字段 next_process_name，
        #    不能误用成外协报价的 q.process_id 对应工序）。
        eligible: list[tuple] = []
        for p in all_parts:
            status = p.status
            location = p.location
            next_proc_name: str | None = None
            next_cat = None
            if p.next_process_id is not None:
                proc = await self.processes.get_by_id(p.next_process_id)
                if proc is not None:
                    next_cat = proc.category
                    next_proc_name = proc.name
            allowed = (
                status == "PENDING"
                or (
                    status == "IN_PROCESS"
                    and location == "PRODUCTION_SHELF"
                    and next_cat == ProcessCategory.OUTSOURCE.value
                )
            )
            if allowed:
                eligible.append((p, next_proc_name))

        # 4. customer 过滤
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is not None:
                cust_ids = await self._expand_customer_ids(cid_int)
                eligible = [
                    (p, n) for p, n in eligible if p.customer_id in cust_ids
                ]

        # 5. keyword 过滤
        if keyword:
            kw = keyword.strip().lower()
            if kw:
                eligible = [
                    (p, n) for p, n in eligible
                    if (p.drawing_no and kw in p.drawing_no.lower())
                    or (p.name and kw in p.name.lower())
                    or (p.serial_no and kw in p.serial_no.lower())
                ]

        eligible.sort(key=lambda t: (
            bool(getattr(t[0], "is_urgent", False)),
            t[0].planned_delivery_date,
        ))

        total = len(eligible)
        page = eligible[offset : offset + limit]

        # 6. 拼装 ApprovedQuoteForSendItem
        items: list[ApprovedQuoteForSendItem] = []
        for p, next_proc_name in page:
            q_for_part = [q for q in all_quotes if q.part_id == p.id]
            if not q_for_part:
                continue
            q = q_for_part[0]
            company = await self.companies.get_by_id(q.outsource_company_id)
            process = await self.processes.get_by_id(q.process_id)
            items.append(ApprovedQuoteForSendItem(
                part_id=p.id,
                part_serial_no=p.serial_no,
                part_drawing_no=p.drawing_no,
                part_name=p.name,
                quantity=p.quantity,
                planned_delivery_date=(
                    p.planned_delivery_date.isoformat()
                    if p.planned_delivery_date else None
                ),
                is_urgent=bool(getattr(p, "is_urgent", False)),
                customer_path=None,
                next_process_id=p.next_process_id,
                next_process_name=next_proc_name,  # 零件的下一道，不是外协工序
                outsource_company_id=q.outsource_company_id,
                outsource_company_name=company.name if company else None,
                process_id=q.process_id,
                process_name=process.name if process else None,
                price=q.price,
            ))

        # 客户路径（一次性查 + cache）
        cust_map: dict[int, TCustomer] = {}
        cust_ids_needed = {p.customer_id for p, _ in page if p.customer_id is not None}
        if cust_ids_needed:
            cust_rows = await self.customers.list_by_ids(list(cust_ids_needed))
            cust_map = {c.id: c for c in cust_rows}
            parent_ids = {c.parent_id for c in cust_map.values() if c.parent_id}
            if parent_ids:
                parent_rows = await self.customers.list_by_ids(list(parent_ids))
                cust_map.update({c.id: c for c in parent_rows})
        for it in items:
            p_obj = next((p for p, _ in page if p.id == it.part_id), None)
            if p_obj and p_obj.customer_id and p_obj.customer_id in cust_map:
                it.customer_path = await self._make_customer_path(  # ← 必须 await
                    cust_map[p_obj.customer_id], cust_map,
                )

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

    async def _expand_customer_ids(self, root_customer_id: int) -> list[int]:
        """L1 + L2 子节点展平。v1 客户树只有 2 层。"""
        ids: list[int] = [root_customer_id]
        children = await self.customers.list_children(root_customer_id)
        if children:
            ids.extend(c.id for c in children)
        return list(dict.fromkeys(ids))

    async def _resolve_part_ids(
        self,
        *,
        customer_id: str | None,
        keyword: str | None,
    ) -> list[int] | None:
        """根据 customer_id / keyword 算出 part_id 集合；None 表示不限。"""
        base_ids: list[int] | None = None
        if customer_id:
            cid_int = parse_snowflake_id(customer_id, field_name="customer_id")
            if cid_int is None:
                return []
            base_ids = await self._expand_customer_ids(cid_int)
        if keyword:
            rows = await self.parts.list_with_filters(
                keyword=keyword,
                include_deleted=False,
                limit=500, offset=0,
            )
            kw_ids = [p.id for p in rows]
            if base_ids is None:
                return kw_ids
            base_set = set(base_ids)
            return [i for i in kw_ids if i in base_set]
        return base_ids

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
        # 单 part_id 直接走 repo
        if part_filter_ids is None:
            rows = await self.quotes.list_with_filters(
                status=status,
                statuses=statuses,
                part_id=part_id,
                outsource_company_id=outsource_company_id,
                sort_by=sort_by, sort_dir=sort_dir,
                limit=limit, offset=offset,
            )
            total = await self.quotes.count_with_filters(
                status=status, statuses=statuses,
                part_id=part_id, outsource_company_id=outsource_company_id,
            )
            return rows, total

        # 多 part_id 遍历（业务量小）
        out: list[TOutsourceQuote] = []
        for pid in part_filter_ids:
            sub = await self.quotes.list_with_filters(
                status=status, statuses=statuses, part_id=pid,
                outsource_company_id=outsource_company_id,
                sort_by=sort_by, sort_dir=sort_dir,
                limit=200, offset=0,
            )
            out.extend(sub)
        out.sort(
            key=lambda q: (
                q.created_at if sort_by == OutsourceQuoteSortKey.CREATED_AT else (
                    q.price if sort_by == OutsourceQuoteSortKey.PRICE else (
                        q.reviewed_at or q.created_at
                    )
                )
            ),
            reverse=(sort_dir == SortDir.DESC),
        )
        total = len(out)
        return out[offset : offset + limit], total

    async def _list_all_approved_quotes(self) -> list[TOutsourceQuote]:
        """直接走 session 拿全部 APPROVED 报价（小数据量假设）。"""
        session: AsyncSession = self.quotes.session
        from sqlalchemy import select as _select
        stmt = _select(TOutsourceQuote).where(
            TOutsourceQuote.deleted_at.is_(None),
            TOutsourceQuote.status == OutsourceQuoteStatus.APPROVED.value,
        ).order_by(
            TOutsourceQuote.part_id.asc(),
            TOutsourceQuote.created_at.desc(),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _to_out(self, q: TOutsourceQuote) -> OutsourceQuoteOut:
        part = await self.parts.get_by_id(q.part_id)
        company = await self.companies.get_by_id(q.outsource_company_id)
        process = await self.processes.get_by_id(q.process_id)

        customer_path: str | None = None
        if part and part.customer_id:
            cust = await self.customers.get_by_id(part.customer_id)
            if cust:
                customer_path = await self._make_customer_path(cust)

        return OutsourceQuoteOut(
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
            created_at=q.created_at,
            updated_at=q.updated_at,
            part_serial_no=part.serial_no if part else None,
            part_drawing_no=part.drawing_no if part else None,
            part_name=part.name if part else None,
            outsource_company_name=company.name if company else None,
            process_code=process.code if process else None,
            process_name=process.name if process else None,
            customer_path=customer_path,
        )

    async def _make_customer_path(
        self, cust: TCustomer, cache: dict | None = None,
    ) -> str | None:
        cache = cache or {}
        cur: TCustomer | None = cust
        path: list[str] = []
        seen: set[int] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            path.append(cur.name)
            if cur.parent_id is None:
                break
            if cur.parent_id in cache:
                cur = cache[cur.parent_id]
            else:
                cur = await self.customers.get_by_id(cur.parent_id)
        path.reverse()
        return " / ".join(path) if path else None
