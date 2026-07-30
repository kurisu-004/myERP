"""外协报价 (OutsourceQuote) 数据访问。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TOutsourceQuote
from model.enums import OutsourceQuoteSortKey, OutsourceQuoteStatus, SortDir


class OutsourceQuoteRepository:
    """t_outsource_quote 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, quote: TOutsourceQuote) -> TOutsourceQuote:
        self.session.add(quote)
        await self.session.flush()
        return quote

    async def update(self, quote: TOutsourceQuote) -> TOutsourceQuote:
        await self.session.flush()
        return quote

    async def soft_delete(self, quote: TOutsourceQuote) -> TOutsourceQuote:
        quote.deleted_at = now_naive()
        await self.session.flush()
        return quote

    # ===== 单条 =====
    async def get_by_id(
        self,
        quote_id: int,
        *,
        include_deleted: bool = False,
    ) -> TOutsourceQuote | None:
        result = await self.session.get(TOutsourceQuote, quote_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_one_approved(
        self,
        *,
        part_id: int,
        outsource_company_id: int,
        process_id: int,
    ) -> TOutsourceQuote | None:
        """发送外协前的防御性校验：找 (part, company, process) 唯一 APPROVED 报价。

        USED 不算（已被别的发送消费过），REJECTED 也不算。
        """
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id == part_id)
            .where(TOutsourceQuote.outsource_company_id == outsource_company_id)
            .where(TOutsourceQuote.process_id == process_id)
            .where(TOutsourceQuote.status == OutsourceQuoteStatus.APPROVED.value)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_one_active_for_tuple(
        self,
        *,
        part_id: int,
        outsource_company_id: int,
        process_id: int,
    ) -> TOutsourceQuote | None:
        """找同一 (part, company, process) 当前 DRAFT/SUBMITTED/APPROVED/OUTSOURCING/RECEIVED/BILLED/USED 的行（用于重复检测）。

        PR-H 2026-07-29：增加 OUTSOURCING / RECEIVED / BILLED 状态（已发送/已接收/已对账
        也算"活跃"行，避免同一 (part, company, process) 在对账完成前重复报价）。
        """
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id == part_id)
            .where(TOutsourceQuote.outsource_company_id == outsource_company_id)
            .where(TOutsourceQuote.process_id == process_id)
            .where(
                TOutsourceQuote.status.in_(
                    [
                        OutsourceQuoteStatus.DRAFT.value,
                        OutsourceQuoteStatus.SUBMITTED.value,
                        OutsourceQuoteStatus.APPROVED.value,
                        OutsourceQuoteStatus.OUTSOURCING.value,
                        OutsourceQuoteStatus.RECEIVED.value,
                        OutsourceQuoteStatus.BILLED.value,
                        OutsourceQuoteStatus.USED.value,
                    ]
                )
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_active_for_part_company_process(
        self,
        *,
        part_id: int,
        company_id: int,
        process_id: int,
        statuses: list[str],
    ) -> TOutsourceQuote | None:
        """PR-H 2026-07-29：按 (part, company, process) 找指定状态列表中的报价行。
        接收外协时用：反查 OUTSOURCING 状态的报价。
        排序：id DESC（最新优先），返回第一条。
        """
        if not statuses:
            return None
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id == part_id)
            .where(TOutsourceQuote.outsource_company_id == company_id)
            .where(TOutsourceQuote.process_id == process_id)
            .where(TOutsourceQuote.status.in_(statuses))
            .order_by(TOutsourceQuote.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_approved_for_part_process(
        self,
        *,
        part_id: int,
        process_id: int,
        is_direct: bool | None = None,
    ) -> TOutsourceQuote | None:
        """2026-07-30：按 (part_id, process_id) 查 APPROVED 报价。

        is_direct=None 时同时查真实报价和 DIRECT 占位，优先返回真实报价。
        """
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id == part_id)
            .where(TOutsourceQuote.process_id == process_id)
            .where(TOutsourceQuote.status == OutsourceQuoteStatus.APPROVED.value)
        )
        if is_direct is not None:
            stmt = stmt.where(TOutsourceQuote.is_direct == is_direct)
        else:
            # 优先真实报价（is_direct=false）
            stmt = stmt.order_by(TOutsourceQuote.is_direct.asc())
        stmt = stmt.order_by(TOutsourceQuote.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_by_part_process(
        self,
        *,
        part_id: int,
        process_id: int,
        exclude_id: int | None = None,
    ) -> list[TOutsourceQuote]:
        """2026-07-30：approve 时自动拒绝同 (part, process) 的其他活跃报价。"""
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id == part_id)
            .where(TOutsourceQuote.process_id == process_id)
            .where(
                TOutsourceQuote.status.in_([
                    OutsourceQuoteStatus.SUBMITTED.value,
                    OutsourceQuoteStatus.APPROVED.value,
                    OutsourceQuoteStatus.DRAFT.value,
                ])
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(TOutsourceQuote.id != exclude_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 对账（已迁移至 t_outsource_shipment，2026-07-30；保留方法供旧代码兼容）=====
    def _build_reconciliation_stmt(
        self,
        *,
        company_id: int,
        part_ids_in: list[int] | None = None,
        sent_from=None,
        sent_to=None,
        received_from=None,
        received_to=None,
    ):
        """对账页共享 statement builder（list / count 复用）。

        谓词：
        - outsource_company_id = company_id
        - status IN (OUTSOURCING, RECEIVED, BILLED) —— 已发送及以后的全部
        - deleted_at IS NULL
        - 可选 part_ids_in（keyword 过滤经 service 在 part_repo 上预先解析）
        - 可选 sent_at / received_at 时间区间
        """
        from model.enums import OutsourceQuoteStatus as _S

        stmt = select(TOutsourceQuote).where(
            TOutsourceQuote.deleted_at.is_(None),
            TOutsourceQuote.outsource_company_id == company_id,
            TOutsourceQuote.status.in_([
                _S.OUTSOURCING.value,
                _S.RECEIVED.value,
                _S.BILLED.value,
            ]),
        )
        if part_ids_in is not None:
            stmt = stmt.where(TOutsourceQuote.part_id.in_(part_ids_in))
        if sent_from is not None:
            stmt = stmt.where(TOutsourceQuote.sent_at >= sent_from)
        if sent_to is not None:
            stmt = stmt.where(TOutsourceQuote.sent_at <= sent_to)
        if received_from is not None:
            stmt = stmt.where(TOutsourceQuote.received_at >= received_from)
        if received_to is not None:
            stmt = stmt.where(TOutsourceQuote.received_at <= received_to)
        return stmt

    async def list_reconciliation_for_company(
        self,
        *,
        company_id: int,
        part_ids_in: list[int] | None = None,
        sent_from=None,
        sent_to=None,
        received_from=None,
        received_to=None,
        sort_by=None,
        sort_dir=None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TOutsourceQuote]:
        """对账页列表：某外协公司 + 已发送状态（OUTSOURCING/RECEIVED/BILLED）的报价行。

        sort_by：OutsourceSentPartSortKey（PRICE / SENT_AT / RECEIVED_AT）。
        NULL 值排序：PostgreSQL 默认 DESC 时 NULLS FIRST / ASC 时 NULLS LAST；
        价格 / 回收时间 NULL 排末尾更符合直觉，用 nulls_last 统一。
        """
        from model.enums import OutsourceSentPartSortKey, SortDir as _Dir

        if part_ids_in is not None and not part_ids_in:
            return []
        stmt = self._build_reconciliation_stmt(
            company_id=company_id,
            part_ids_in=part_ids_in,
            sent_from=sent_from, sent_to=sent_to,
            received_from=received_from, received_to=received_to,
        )
        if sort_by == OutsourceSentPartSortKey.PRICE:
            col = TOutsourceQuote.price
        elif sort_by == OutsourceSentPartSortKey.RECEIVED_AT:
            col = TOutsourceQuote.received_at
        else:
            col = TOutsourceQuote.sent_at
        order = col.asc().nulls_last() if sort_dir == _Dir.ASC else col.desc().nulls_last()
        stmt = stmt.order_by(order, TOutsourceQuote.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_reconciliation_for_company(
        self,
        *,
        company_id: int,
        part_ids_in: list[int] | None = None,
        sent_from=None,
        sent_to=None,
        received_from=None,
        received_to=None,
    ) -> int:
        """对账页总数（与 list_reconciliation_for_company 同谓词）。"""
        if part_ids_in is not None and not part_ids_in:
            return 0
        stmt = self._build_reconciliation_stmt(
            company_id=company_id,
            part_ids_in=part_ids_in,
            sent_from=sent_from, sent_to=sent_to,
            received_from=received_from, received_to=received_to,
        ).with_only_columns(func.count(TOutsourceQuote.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 批量 =====
    async def list_by_part_with_status(
        self,
        part_id: int,
        status: str | None = None,
    ) -> list[TOutsourceQuote]:
        """按 part + 可选 status 取报价（前端「外协发送」列表 + 历史展示用）。"""
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id == part_id)
        )
        if status:
            stmt = stmt.where(TOutsourceQuote.status == status)
        stmt = stmt.order_by(TOutsourceQuote.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_approved_for_part_ids(
        self,
        part_ids: list[int],
    ) -> list[TOutsourceQuote]:
        """批量取一组 part 的 APPROVED 报价，外协发送列表页用。

        返回的每条记录对应一个 (part_id, company_id, process_id)；
        多家公司时一条 part 可能有多条返回。
        """
        if not part_ids:
            return []
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.part_id.in_(part_ids))
            .where(TOutsourceQuote.status == OutsourceQuoteStatus.APPROVED.value)
        )
        stmt = stmt.order_by(
            TOutsourceQuote.part_id.asc(),
            TOutsourceQuote.created_at.desc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_approved(self) -> list[TOutsourceQuote]:
        """全部 APPROVED 报价（外协发送页用；小数据量假设）。

        排序 part_id ASC, created_at DESC, id DESC —— 让「每个 part 取第一条」
        的选择确定（同 part 多家公司时取最新创建的一条）。
        """
        stmt = (
            select(TOutsourceQuote)
            .where(TOutsourceQuote.deleted_at.is_(None))
            .where(TOutsourceQuote.status == OutsourceQuoteStatus.APPROVED.value)
            .order_by(
                TOutsourceQuote.part_id.asc(),
                TOutsourceQuote.created_at.desc(),
                TOutsourceQuote.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表（报价一览页）=====
    async def list_with_filters(
        self,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        part_id: int | None = None,
        part_ids_in: list[int] | None = None,
        outsource_company_id: int | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        sort_by: OutsourceQuoteSortKey = OutsourceQuoteSortKey.CREATED_AT,
        sort_dir: SortDir = SortDir.DESC,
        # customer / keyword 过滤由 service 先在 part_repo 上算出 part_ids_in 再传入本方法
        limit: int = 50,
        offset: int = 0,
    ) -> list[TOutsourceQuote]:
        # part_ids_in == [] → 无匹配零件，直接空结果（不发 SQL）
        if part_ids_in is not None and not part_ids_in:
            return []
        stmt = self._build_filter_stmt(
            status=status,
            statuses=statuses,
            part_id=part_id,
            part_ids_in=part_ids_in,
            outsource_company_id=outsource_company_id,
        )
        stmt = stmt.order_by(*self._order_cols(sort_by, sort_dir))
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        status: str | None = None,
        statuses: list[str] | None = None,
        part_id: int | None = None,
        part_ids_in: list[int] | None = None,
        outsource_company_id: int | None = None,
    ) -> int:
        if part_ids_in is not None and not part_ids_in:
            return 0
        stmt = self._build_filter_stmt(
            status=status,
            statuses=statuses,
            part_id=part_id,
            part_ids_in=part_ids_in,
            outsource_company_id=outsource_company_id,
        ).with_only_columns(func.count(TOutsourceQuote.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 内部：filter builder + 排序列 =====
    def _build_filter_stmt(
        self,
        *,
        status: str | None,
        statuses: list[str] | None,
        part_id: int | None,
        part_ids_in: list[int] | None,
        outsource_company_id: int | None,
    ):
        stmt = select(TOutsourceQuote).where(TOutsourceQuote.deleted_at.is_(None))
        effective_statuses: list[str] = []
        if status:
            effective_statuses.append(status)
        if statuses:
            effective_statuses.extend(statuses)
        if len(effective_statuses) == 1:
            stmt = stmt.where(TOutsourceQuote.status == effective_statuses[0])
        elif len(effective_statuses) > 1:
            stmt = stmt.where(TOutsourceQuote.status.in_(effective_statuses))
        if part_id is not None:
            stmt = stmt.where(TOutsourceQuote.part_id == part_id)
        if part_ids_in is not None:
            # 空列表已在调用处短路；此处非空
            stmt = stmt.where(TOutsourceQuote.part_id.in_(part_ids_in))
        if outsource_company_id is not None:
            stmt = stmt.where(TOutsourceQuote.outsource_company_id == outsource_company_id)
        return stmt

    def _order_cols(self, sort_by: OutsourceQuoteSortKey, sort_dir: SortDir):
        """把 sort_by 落到真实列 + id tie-break（保证分页确定性）。"""
        if sort_by == OutsourceQuoteSortKey.PRICE:
            col = TOutsourceQuote.price
        elif sort_by == OutsourceQuoteSortKey.REVIEWED_AT:
            col = func.coalesce(
                TOutsourceQuote.reviewed_at, TOutsourceQuote.created_at,
            )
        else:  # CREATED_AT 默认
            col = TOutsourceQuote.created_at
        if sort_dir == SortDir.ASC:
            return (col.asc(), TOutsourceQuote.id.asc())
        return (col.desc(), TOutsourceQuote.id.desc())
