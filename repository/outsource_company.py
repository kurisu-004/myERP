"""外协公司 (OutsourceCompany) 数据访问。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TOutsourceCompany


class OutsourceCompanyRepository:
    """t_outsource_company 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, company: TOutsourceCompany) -> TOutsourceCompany:
        self.session.add(company)
        await self.session.flush()
        return company

    async def update(self, company: TOutsourceCompany) -> TOutsourceCompany:
        await self.session.flush()
        return company

    async def soft_delete(
        self, company: TOutsourceCompany,
    ) -> TOutsourceCompany:
        company.deleted_at = now_naive()
        await self.session.flush()
        return company

    # ===== 单条 =====
    async def get_by_id(
        self,
        company_id: int,
        *,
        include_deleted: bool = False,
    ) -> TOutsourceCompany | None:
        result = await self.session.get(TOutsourceCompany, company_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_name(
        self,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> TOutsourceCompany | None:
        """按 name 精确查（用于唯一性预校验）。"""
        stmt = select(TOutsourceCompany).where(TOutsourceCompany.name == name)
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompany.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 批量 =====
    async def list_by_ids(
        self,
        ids: list[int],
        *,
        include_deleted: bool = False,
    ) -> list[TOutsourceCompany]:
        if not ids:
            return []
        stmt = select(TOutsourceCompany).where(TOutsourceCompany.id.in_(ids))
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompany.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表 =====
    async def list_with_filters(
        self,
        *,
        name_like: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TOutsourceCompany]:
        stmt = select(TOutsourceCompany)
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompany.deleted_at.is_(None))
        if name_like:
            stmt = stmt.where(TOutsourceCompany.name.ilike(f"%{name_like}%"))
        if is_active is not None:
            stmt = stmt.where(TOutsourceCompany.is_active.is_(is_active))
        stmt = stmt.order_by(
            TOutsourceCompany.is_active.desc(),
            TOutsourceCompany.name.asc(),
            TOutsourceCompany.id.asc(),
        )
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        name_like: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TOutsourceCompany)
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompany.deleted_at.is_(None))
        if name_like:
            stmt = stmt.where(TOutsourceCompany.name.ilike(f"%{name_like}%"))
        if is_active is not None:
            stmt = stmt.where(TOutsourceCompany.is_active.is_(is_active))
        stmt = stmt.with_only_columns(func.count(TOutsourceCompany.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())