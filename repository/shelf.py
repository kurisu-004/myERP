from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TShelf


class ShelfRepository:
    """t_shelf 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, shelf: TShelf) -> TShelf:
        self.session.add(shelf)
        await self.session.flush()
        return shelf

    async def update(self, shelf: TShelf) -> TShelf:
        await self.session.flush()
        return shelf

    async def soft_delete(self, shelf: TShelf) -> TShelf:
        shelf.deleted_at = datetime.utcnow()
        shelf.is_active = False
        await self.session.flush()
        return shelf

    # ===== 单条 =====
    async def get_by_id(
        self, shelf_id: int, *, include_deleted: bool = False
    ) -> TShelf | None:
        result = await self.session.get(TShelf, shelf_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_code(
        self, code: str, *, include_deleted: bool = False
    ) -> TShelf | None:
        stmt = select(TShelf).where(TShelf.code == code)
        if not include_deleted:
            stmt = stmt.where(TShelf.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_ids(self, ids: list[int]) -> list[TShelf]:
        if not ids:
            return []
        stmt = select(TShelf).where(
            TShelf.id.in_(ids),
            TShelf.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_by_zone(self, zone: str) -> list[TShelf]:
        stmt = (
            select(TShelf)
            .where(
                TShelf.zone == zone,
                TShelf.is_active.is_(True),
                TShelf.deleted_at.is_(None),
            )
            .order_by(TShelf.code.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表 =====
    async def list_with_filters(
        self,
        *,
        zone: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[TShelf]:
        stmt = select(TShelf)
        if not include_deleted:
            stmt = stmt.where(TShelf.deleted_at.is_(None))
        if zone:
            stmt = stmt.where(TShelf.zone == zone)
        if is_active is not None:
            stmt = stmt.where(TShelf.is_active.is_(is_active))
        stmt = stmt.order_by(TShelf.zone.asc(), TShelf.code.asc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        zone: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TShelf)
        if not include_deleted:
            stmt = stmt.where(TShelf.deleted_at.is_(None))
        if zone:
            stmt = stmt.where(TShelf.zone == zone)
        if is_active is not None:
            stmt = stmt.where(TShelf.is_active.is_(is_active))
        stmt = stmt.with_only_columns(func.count(TShelf.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_with_in_use_check_ids(
        self, ids: list[int]
    ) -> dict[int, TShelf]:
        return {s.id: s for s in await self.list_by_ids(ids)}
