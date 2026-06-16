"""账号仓储."""
from datetime import datetime
from datetime import timezone
from typing import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TUser


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_paginated(
        self,
        page: int = 1,
        size: int = 20,
        keyword: str | None = None,
    ) -> tuple[list[TUser], int]:
        stmt = select(TUser).where(TUser.deleted_at.is_(None))
        count_stmt = (
            select(func.count())
            .select_from(TUser)
            .where(TUser.deleted_at.is_(None))
        )
        if keyword:
            like = f"%{keyword}%"
            cond = or_(
                TUser.username.ilike(like),
                TUser.name.ilike(like),
                TUser.employee_no.ilike(like),
            )
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        total = (await self.session.execute(count_stmt)).scalar_one()
        offset = (page - 1) * size
        rows = await self.session.execute(
            stmt.order_by(TUser.id).offset(offset).limit(size)
        )
        return list(rows.scalars().all()), total

    async def get_by_id(
        self, user_id: int, include_deleted: bool = False
    ) -> TUser | None:
        u = await self.session.get(TUser, user_id)
        if u is None:
            return None
        if not include_deleted and u.deleted_at is not None:
            return None
        return u

    async def get_by_username(
        self,
        username: str,
        include_deleted: bool = False,
        include_inactive: bool = False,
    ) -> TUser | None:
        stmt = select(TUser).where(TUser.username == username)
        if not include_deleted:
            stmt = stmt.where(TUser.deleted_at.is_(None))
        if not include_inactive:
            stmt = stmt.where(TUser.is_active == 1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_employee_no(
        self, employee_no: str, include_deleted: bool = False
    ) -> TUser | None:
        stmt = select(TUser).where(TUser.employee_no == employee_no)
        if not include_deleted:
            stmt = stmt.where(TUser.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, user: TUser) -> TUser:
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user: TUser) -> TUser:
        await self.session.flush()
        return user

    async def update_last_login(self, user_id: int, ts: datetime) -> None:
        u = await self.session.get(TUser, user_id)
        if u is None:
            return
        u.last_login_at = ts.replace(tzinfo=None) if ts.tzinfo else ts
        await self.session.flush()

    async def soft_delete(self, user: TUser) -> None:
        user.deleted_at = _utcnow_naive()
        await self.session.flush()
