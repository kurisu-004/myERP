"""外协公司 (OutsourceCompany) 业务逻辑层。

设计要点：
- 与 CustomerService / WorkTypeProcessService 形态对称。
- `set_outsource_company_processes` 走整体替换（delete-then-insert）。
- 入参 ID 是雪花 ID 字符串（CLAUDE.md §3），service 层 parse_snowflake_id 转 int。
- 2026-07-28：`list_sent_parts` 外协对账端点，按公司聚合 SENT_TO_OUTSOURCE 事件。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TOutsourceCompany, TOutsourceCompanyProcess, TProcess
from model.enums import PartEventType, PartLocation, PartStatus, ProcessCategory
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_company_process import OutsourceCompanyProcessRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from schema.outsource_company import (
    OutsourceCompanyCreateRequest,
    OutsourceCompanyListOut,
    OutsourceCompanyListQuery,
    OutsourceCompanyOut,
    OutsourceCompanyProcessLinkOut,
    OutsourceCompanyUpdateRequest,
    OutsourceCompanyWithProcessesOut,
    OutsourceSentPartItem,
    OutsourceSentPartListOut,
    OutsourceSentPartListQuery,
    SetOutsourceCompanyProcessRequest,
)
from service._id_parse import parse_snowflake_id
from utils.id_gen import new_id


class OutsourceCompanyService:
    def __init__(
        self,
        companies: OutsourceCompanyRepository,
        junction: OutsourceCompanyProcessRepository,
        processes: ProcessRepository,
        part_repo: PartRepository | None = None,
        part_events: PartEventRepository | None = None,
        outsource_quotes: OutsourceQuoteRepository | None = None,
        customers: CustomerRepository | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.companies = companies
        self.junction = junction
        self.processes = processes
        self.part_repo = part_repo
        self.part_events = part_events
        # PR-H 2026-07-29：对账页改为基于 t_outsource_quote
        self.outsource_quotes = outsource_quotes
        self.customers = customers
        self._user_id: int | None = current_user.id if current_user else None

    # ============================================================
    # 查询
    # ============================================================
    async def list_companies(
        self, query: OutsourceCompanyListQuery,
    ) -> OutsourceCompanyListOut:
        rows = await self.companies.list_with_filters(
            name_like=query.name_like,
            is_active=query.is_active,
            include_deleted=False,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.companies.count_with_filters(
            name_like=query.name_like,
            is_active=query.is_active,
            include_deleted=False,
        )
        return OutsourceCompanyListOut(
            items=[self._to_out(c) for c in rows],
            total=total,
            limit=query.limit,
            offset=query.offset,
        )

    async def get_company(self, company_id: str) -> OutsourceCompanyWithProcessesOut:
        cid = parse_snowflake_id(company_id, field_name="company_id")
        if cid is None:
            raise self._not_found(company_id)
        company = await self.companies.get_by_id(cid)
        if company is None:
            raise self._not_found(company_id)
        return await self._to_with_processes(company)

    async def list_companies_for_process(
        self, process_id: int,
    ) -> list[OutsourceCompanyOut]:
        """发送外协对话框：列出能做该 OUTSOURCE 工序的所有 active 公司。"""
        rows = await self.junction.list_companys_by_process(
            process_id, include_deleted=False,
        )
        company_ids = [r.outsource_company_id for r in rows]
        if not company_ids:
            return []
        companies = await self.companies.list_by_ids(company_ids)
        # 只保留 is_active=True 的；按名称排序
        return [
            self._to_out(c) for c in companies if c.is_active
        ]

    # ============================================================
    # 外协对账（2026-07-28 新增；2026-07-29 基于 t_outsource_quote 重写）
    # ============================================================
    async def list_sent_parts(
        self,
        company_id: str,
        query: OutsourceSentPartListQuery,
    ) -> OutsourceSentPartListOut:
        """外协对账：列出发送给该外协公司所有零件一览（与对账单核对）。

        PR-H 2026-07-29：数据源从 t_part_event 改为 t_outsource_quote ——
        该表已包含 part_id / company_id / process_id / price / quantity /
        sent_at / received_at / status / is_billed 等一切对账所需字段。

        过滤：keyword（图号/名称，经 part_repo 预解析为 part_ids_in）、
        sent_from / sent_to / received_from / received_to（时间区间）、
        sort_by（PRICE / SENT_AT / RECEIVED_AT）+ sort_dir。
        """
        cid = parse_snowflake_id(company_id, field_name="company_id")
        if cid is None:
            raise self._not_found(company_id)
        company = await self.companies.get_by_id(cid)
        if company is None:
            raise self._not_found(company_id)

        if self.outsource_quotes is None or self.part_repo is None:
            return OutsourceSentPartListOut(
                items=[], total=0, limit=query.limit, offset=query.offset,
            )

        # 1. keyword → part_ids_in（drawing_no / name ILIKE）
        part_ids_in: list[int] | None = None
        if query.keyword and query.keyword.strip():
            kw = query.keyword.strip()
            part_rows = await self.part_repo.list_with_filters(keyword=kw, limit=10_000)
            part_ids_in = [p.id for p in part_rows]
            if not part_ids_in:
                return OutsourceSentPartListOut(
                    items=[], total=0, limit=query.limit, offset=query.offset,
                )

        # 2. 查 t_outsource_quote（已发送状态：OUTSOURCING / RECEIVED / BILLED）
        total = await self.outsource_quotes.count_reconciliation_for_company(
            company_id=cid,
            part_ids_in=part_ids_in,
            sent_from=query.sent_from, sent_to=query.sent_to,
            received_from=query.received_from, received_to=query.received_to,
        )
        if total == 0:
            return OutsourceSentPartListOut(
                items=[], total=0, limit=query.limit, offset=query.offset,
            )
        quotes = await self.outsource_quotes.list_reconciliation_for_company(
            company_id=cid,
            part_ids_in=part_ids_in,
            sent_from=query.sent_from, sent_to=query.sent_to,
            received_from=query.received_from, received_to=query.received_to,
            sort_by=query.sort_by, sort_dir=query.sort_dir,
            limit=query.limit, offset=query.offset,
        )

        # 3. 批查 part / process / customer（输出拼装用）
        part_ids = list({q.part_id for q in quotes})
        parts = await self.part_repo.list_by_ids(part_ids) if part_ids else []
        part_map = {p.id: p for p in parts}

        process_ids = list({q.process_id for q in quotes})
        proc_map = {}
        if process_ids:
            procs = await self.processes.list_by_ids(process_ids)
            proc_map = {p.id: p for p in procs}

        # customer_path（一级 / 二级）：复用 customer 批查 helper
        from service._customer_helpers import make_customer_path_cached, preload_customer_cache
        cust_cache = await preload_customer_cache(
            self.customers,
            [p.customer_id for p in parts if p.customer_id],
        ) if self.customers is not None else {}

        # 4. 拼装
        items: list[OutsourceSentPartItem] = []
        for q in quotes:
            p = part_map.get(q.part_id)
            proc = proc_map.get(q.process_id)
            customer_path: str | None = None
            if p and p.customer_id and p.customer_id in cust_cache:
                customer_path = make_customer_path_cached(
                    cust_cache[p.customer_id], cust_cache,
                )
            total_price = None
            if q.price is not None and q.quantity is not None:
                total_price = q.price * q.quantity
            items.append(OutsourceSentPartItem(
                quote_id=q.id,
                version=q.version,
                part_id=q.part_id,
                part_drawing_no=p.drawing_no if p else None,
                part_name=p.name if p else None,
                customer_path=customer_path,
                process_id=q.process_id,
                process_name=proc.name if proc else None,
                quantity=q.quantity,
                unit_price=q.price,
                total_price=total_price,
                sent_at=q.sent_at,
                received_at=q.received_at,
                status=q.status,
                is_billed=bool(getattr(q, "is_billed", False)),
            ))
        return OutsourceSentPartListOut(
            items=items, total=total,
            limit=query.limit, offset=query.offset,
        )

    # ============================================================
    # 写
    # ============================================================
    async def create_company(
        self, data: OutsourceCompanyCreateRequest,
    ) -> OutsourceCompanyWithProcessesOut:
        name = data.name.strip()
        # 预校验：同名校验（应用层 + DB 部分唯一索引双重兜底）
        existing = await self.companies.get_by_name(name)
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE,
                message=f"外协公司「{name}」已存在",
                http_status=http_status.HTTP_409_CONFLICT,
            )

        company = TOutsourceCompany(
            id=new_id(),
            name=name,
            contact_name=(data.contact_name or None),
            contact_phone=(data.contact_phone or None),
            address=(data.address or None),
            is_active=data.is_active,
        )
        company.created_by = self._user_id
        company.updated_by = self._user_id
        try:
            await self.companies.create(company)
        except IntegrityError as e:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE,
                message=f"外协公司「{name}」已存在",
                http_status=http_status.HTTP_409_CONFLICT,
            ) from e

        # 若 payload 带 process_ids 则一并写入映射（整体替换语义：当前为空 → 直接 insert）
        if data.process_ids:
            int_ids = self._parse_process_ids(data.process_ids)
            await self._replace_processes(
                company.id, int_ids,
                expect_category=ProcessCategory.OUTSOURCE.value,
            )

        # 刷新返回（带映射）
        return await self._to_with_processes(company)

    async def update_company(
        self,
        company_id: str,
        data: OutsourceCompanyUpdateRequest,
    ) -> OutsourceCompanyWithProcessesOut:
        cid = parse_snowflake_id(company_id, field_name="company_id")
        if cid is None:
            raise self._not_found(company_id)
        company = await self.companies.get_by_id(cid)
        if company is None:
            raise self._not_found(company_id)

        if data.name is not None:
            new_name = data.name.strip()
            if new_name != company.name:
                existing = await self.companies.get_by_name(new_name)
                if existing is not None and existing.id != company.id:
                    raise BizError(
                        code=ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE,
                        message=f"外协公司「{new_name}」已存在",
                        http_status=http_status.HTTP_409_CONFLICT,
                    )
                company.name = new_name
        if data.contact_name is not None:
            company.contact_name = data.contact_name or None
        if data.contact_phone is not None:
            company.contact_phone = data.contact_phone or None
        if data.address is not None:
            company.address = data.address or None
        if data.is_active is not None:
            company.is_active = data.is_active

        company.updated_by = self._user_id
        try:
            await self.companies.update(company)
        except IntegrityError as e:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE,
                message=f"外协公司「{company.name}」已存在",
                http_status=http_status.HTTP_409_CONFLICT,
            ) from e

        return await self._to_with_processes(company)

    async def soft_delete_company(self, company_id: str) -> None:
        cid = parse_snowflake_id(company_id, field_name="company_id")
        if cid is None:
            raise self._not_found(company_id)
        company = await self.companies.get_by_id(cid)
        if company is None:
            raise self._not_found(company_id)
        # v1：仍有未软删的映射行 → 拒（避免悬空）。后续如需要可放宽到允许停用。
        rows = await self.junction.list_by_outsource_company(
            cid, include_deleted=False,
        )
        if rows:
            raise BizError(
                code=ErrCode.BIZ_OUTSOURCE_COMPANY_IN_USE,
                message=(
                    f"外协公司「{company.name}」仍映射 {len(rows)} 项工序，"
                    "请先在「维护工序」中清空"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        company.updated_by = self._user_id
        await self.companies.soft_delete(company)

    # ============================================================
    # 整体替换映射
    # ============================================================
    async def set_outsource_company_processes(
        self,
        company_id: str,
        data: SetOutsourceCompanyProcessRequest,
    ) -> OutsourceCompanyWithProcessesOut:
        cid = parse_snowflake_id(company_id, field_name="company_id")
        if cid is None:
            raise self._not_found(company_id)
        company = await self.companies.get_by_id(cid)
        if company is None:
            raise self._not_found(company_id)

        int_ids = self._parse_process_ids(data.process_ids)
        await self._replace_processes(
            cid, int_ids,
            expect_category=ProcessCategory.OUTSOURCE.value,
        )
        return await self._to_with_processes(company)

    # ============================================================
    # 内部
    # ============================================================
    def _not_found(self, company_id: str | int) -> BizError:
        return BizError(
            code=ErrCode.BIZ_OUTSOURCE_COMPANY_NOT_FOUND,
            message=f"outsource company {company_id!r} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )

    def _parse_process_ids(self, raw: list[str]) -> list[int]:
        """把 request body 里的 process_id 字符串列表（雪花 ID）转 int。

        任一元素无法解析 → 抛 BIZ_INVALID_VALUE 400（与 parse_snowflake_id 一致）。
        """
        out: list[int] = []
        for raw_id in raw:
            parsed = parse_snowflake_id(raw_id, field_name="process_ids[]")
            if parsed is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=f"process_id 不是合法的雪花 ID 字符串：{raw_id!r}",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            out.append(parsed)
        return out

    def _to_out(self, c: TOutsourceCompany) -> OutsourceCompanyOut:
        return OutsourceCompanyOut(
            id=c.id,
            version=c.version,
            name=c.name,
            contact_name=c.contact_name,
            contact_phone=c.contact_phone,
            address=c.address,
            is_active=c.is_active,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )

    async def _to_with_processes(
        self, c: TOutsourceCompany,
    ) -> OutsourceCompanyWithProcessesOut:
        rows = await self.junction.list_by_outsource_company(
            c.id, include_deleted=False,
        )
        process_ids = [r.process_id for r in rows]
        process_map: dict[int, TProcess] = {}
        if process_ids:
            procs = await self.processes.list_by_ids(process_ids)
            process_map = {p.id: p for p in procs}
        links = [
            OutsourceCompanyProcessLinkOut(
                process_id=r.process_id,
                process_code=process_map[r.process_id].code
                if r.process_id in process_map
                else "",
                process_name=process_map[r.process_id].name
                if r.process_id in process_map
                else "",
                category=process_map[r.process_id].category
                if r.process_id in process_map
                else ProcessCategory.OUTSOURCE,
                sort_order=r.sort_order,
            )
            for r in rows
        ]
        return OutsourceCompanyWithProcessesOut(
            id=c.id,
            version=c.version,
            name=c.name,
            contact_name=c.contact_name,
            contact_phone=c.contact_phone,
            address=c.address,
            is_active=c.is_active,
            created_at=c.created_at,
            updated_at=c.updated_at,
            processes=links,
        )

    async def _replace_processes(
        self,
        company_id: int,
        process_ids: list[int],
        *,
        expect_category: str,
    ) -> None:
        """整体替换某公司的工序映射（含校验 + 软删 + 批量插入）。

        与 WorkTypeProcessService.set_for_work_type 同样的 5 步协议。
        """
        # 1. 去重保序
        seen: set[int] = set()
        ordered_ids: list[int] = []
        for pid in process_ids:
            if pid in seen:
                continue
            seen.add(pid)
            ordered_ids.append(pid)

        # 2. 校验全部 process 存在 + 类别匹配
        if ordered_ids:
            procs = await self.processes.list_by_ids(ordered_ids)
            found_ids = {p.id for p in procs}
            missing = [pid for pid in ordered_ids if pid not in found_ids]
            if missing:
                raise BizError(
                    code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                    message=f"process not found: {missing}",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            bad_category = [
                p.id for p in procs if p.category != expect_category
            ]
            if bad_category:
                raise BizError(
                    code=ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS,
                    message=(
                        f"工序 id {bad_category} 不是 {expect_category} 类别，"
                        "不能映射到外协公司"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

        # 3. 软删现有映射（先给每行赋 updated_by，让 audit 字段一并写入）
        existing = await self.junction.list_by_outsource_company(
            company_id, include_deleted=False,
        )
        for row in existing:
            row.updated_by = self._user_id
        await self.junction.delete_by_outsource_company(company_id)

        # 4. 批量插入新映射
        new_rows: list[TOutsourceCompanyProcess] = []
        for idx, pid in enumerate(ordered_ids):
            new_rows.append(TOutsourceCompanyProcess(
                id=new_id(),
                outsource_company_id=company_id,
                process_id=pid,
                sort_order=idx,
            ))
        for row in new_rows:
            row.created_by = self._user_id
            row.updated_by = self._user_id
            await self.junction.create(row)