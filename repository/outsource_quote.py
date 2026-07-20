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
        """找同一 (part, company, process) 当前 DRAFT/SUBMITTED/APPROVED 的行（用于重复检测）。"""
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
                        OutsourceQuoteStatus.USED.value,
                    ]
                )
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

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
