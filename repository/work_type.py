"""工种 (WorkType) 数据访问。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TWorkType


class WorkTypeRepository:
    """t_work_type 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, work_type: TWorkType) -> TWorkType:
        self.session.add(work_type)
        await self.session.flush()
        return work_type

    # ===== 单条 =====
    async def get_by_id(
        self, work_type_id: int, *, include_deleted: bool = False
    ) -> TWorkType | None:
        result = await self.session.get(TWorkType, work_type_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_code(
        self, code: str, *, include_deleted: bool = False
    ) -> TWorkType | None:
        stmt = select(TWorkType).where(TWorkType.code == code)
        if not include_deleted:
            stmt = stmt.where(TWorkType.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_ids(
        self, work_type_ids: list[int], *, include_deleted: bool = False
    ) -> list[TWorkType]:
        if not work_type_ids:
            return []
        stmt = select(TWorkType).where(TWorkType.id.in_(work_type_ids))
        if not include_deleted:
            stmt = stmt.where(TWorkType.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表 =====
    async def list_with_filters(
        self,
        *,
        code_like: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TWorkType]:
        stmt = select(TWorkType)
        if not include_deleted:
            stmt = stmt.where(TWorkType.deleted_at.is_(None))
        if code_like:
            stmt = stmt.where(TWorkType.code.ilike(f"%{code_like}%"))
        stmt = stmt.order_by(TWorkType.sort_order.asc(), TWorkType.id.asc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        code_like: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TWorkType)
        if not include_deleted:
            stmt = stmt.where(TWorkType.deleted_at.is_(None))
        if code_like:
            stmt = stmt.where(TWorkType.code.ilike(f"%{code_like}%"))
        stmt = stmt.with_only_columns(func.count(TWorkType.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 软删 =====
    async def update(self, work_type: TWorkType) -> TWorkType:
        await self.session.flush()
        return work_type

    async def soft_delete(self, work_type: TWorkType) -> TWorkType:
        work_type.deleted_at = now_naive()
        await self.session.flush()
        return work_type