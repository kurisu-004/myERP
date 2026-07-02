from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart
from model.enums import PartSortKey, PartStatus, SortDir


class PartRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, part: TPart) -> TPart:
        self.session.add(part)
        await self.session.flush()
        return part

    async def create_many(self, parts: list[TPart]) -> list[TPart]:
        self.session.add_all(parts)
        await self.session.flush()
        return parts

    # ===== 单条查询 =====
    async def get_by_id(
        self, part_id: int, *, include_deleted: bool = False
    ) -> TPart | None:
        part = await self.session.get(TPart, part_id)
        if part is None:
            return None
        if not include_deleted and part.deleted_at is not None:
            return None
        return part

    async def get_by_drawing_no(
        self, drawing_no: str, *, include_deleted: bool = False
    ) -> TPart | None:
        """按图号精确匹配（车间扫码定位用）。返回最近一条未软删记录。"""
        stmt = select(TPart).where(TPart.drawing_no == drawing_no)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        stmt = stmt.order_by(TPart.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表查询（核心：前缀搜索 + 多维过滤 + 排序） =====
    async def list_with_filters(
        self,
        *,
        customer_id: int | None = None,
        statuses: list[PartStatus] | None = None,
        is_urgent: bool | None = None,
        keyword: str | None = None,
        sort_by: PartSortKey = PartSortKey.PLANNED_DELIVERY_DATE,
        sort_dir: SortDir = SortDir.ASC,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TPart]:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            statuses=statuses,
            is_urgent=is_urgent,
            keyword=keyword,
            include_deleted=include_deleted,
        )
        sort_col = {
            PartSortKey.PLANNED_DELIVERY_DATE: TPart.planned_delivery_date,
            PartSortKey.REQUEST_DATE: TPart.request_date,
            PartSortKey.CREATED_AT: TPart.created_at,
        }[sort_by]
        if sort_dir == SortDir.ASC:
            stmt = stmt.order_by(sort_col.asc(), TPart.id.desc())
        else:
            stmt = stmt.order_by(sort_col.desc(), TPart.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        customer_id: int | None = None,
        statuses: list[PartStatus] | None = None,
        is_urgent: bool | None = None,
        keyword: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            statuses=statuses,
            is_urgent=is_urgent,
            keyword=keyword,
            include_deleted=include_deleted,
        ).with_only_columns(func.count(TPart.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 删除（软删） =====
    async def update(self, part: TPart) -> TPart:
        await self.session.flush()
        return part

    async def soft_delete(self, part: TPart) -> TPart:
        part.deleted_at = datetime.utcnow()
        await self.session.flush()
        return part

    # ===== 序列号查询 =====
    async def get_by_serial(
        self, serial_no: str, *, include_deleted: bool = False
    ) -> TPart | None:
        stmt = select(TPart).where(TPart.serial_no == serial_no)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 装配体关联 =====
    async def list_children(self, assembly_id: int) -> list[TPart]:
        """取装配件的所有子零件，按 drawing_no 升序（保证 PDF 页顺序）。"""
        stmt = (
            select(TPart)
            .where(
                TPart.assembly_id == assembly_id,
                TPart.deleted_at.is_(None),
            )
            .order_by(TPart.drawing_no.asc(), TPart.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 内部 =====
    def _build_filter_stmt(
        self,
        *,
        customer_id: int | None,
        statuses: list[PartStatus] | None,
        is_urgent: bool | None,
        keyword: str | None,
        include_deleted: bool,
    ):
        stmt = select(TPart)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        if customer_id is not None:
            stmt = stmt.where(TPart.customer_id == customer_id)
        if statuses:
            stmt = stmt.where(
                TPart.status.in_([s.value for s in statuses])
            )
        if is_urgent is not None:
            stmt = stmt.where(TPart.is_urgent.is_(is_urgent))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                )
        return stmt