"""角色仓储."""
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TRole


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, role: TRole) -> TRole:
        self.session.add(role)
        await self.session.flush()
        return role

    async def get_by_id(
        self, role_id: int, include_deleted: bool = False
    ) -> TRole | None:
        role = await self.session.get(TRole, role_id)
        if role is None:
            return None
        if not include_deleted and role.deleted_at is not None:
            return None
        return role

    async def get_by_code(
        self, code: str, include_deleted: bool = False
    ) -> TRole | None:
        stmt = select(TRole).where(TRole.code == code)
        if not include_deleted:
            stmt = stmt.where(TRole.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, include_deleted: bool = False) -> list[TRole]:
        stmt = select(TRole)
        if not include_deleted:
            stmt = stmt.where(TRole.deleted_at.is_(None))
        stmt = stmt.order_by(TRole.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_paginated(
        self,
        page: int = 1,
        size: int = 20,
        keyword: str | None = None,
    ) -> tuple[list[TRole], int]:
        stmt = select(TRole).where(TRole.deleted_at.is_(None))
        count_stmt = (
            select(func.count())
            .select_from(TRole)
            .where(TRole.deleted_at.is_(None))
        )
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                (TRole.code.ilike(like)) | (TRole.name.ilike(like))
            )
            count_stmt = count_stmt.where(
                (TRole.code.ilike(like)) | (TRole.name.ilike(like))
            )
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(TRole.id).offset((page - 1) * size).limit(size)
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows), total

    async def update(self, role: TRole) -> TRole:
        await self.session.flush()
        return role

    async def soft_delete(self, role: TRole) -> None:
        if role.is_builtin:
            raise ValueError("builtin role cannot be deleted")
        from datetime import datetime
        from datetime import timezone

        role.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()
