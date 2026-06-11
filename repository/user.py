from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TUser


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_paginated(self, page: int, size: int) -> tuple[list[TUser], int]:
        offset = (page - 1) * size
        total = (
            await self.session.execute(select(func.count()).select_from(TUser))
        ).scalar_one()
        rows = await self.session.execute(
            select(TUser).order_by(TUser.id).offset(offset).limit(size)
        )
        return list(rows.scalars().all()), total

    async def get_by_id(self, user_id: int) -> TUser | None:
        return await self.session.get(TUser, user_id)

    async def get_by_username(self, username: str) -> TUser | None:
        result = await self.session.execute(
            select(TUser).where(TUser.username == username)
        )
        return result.scalar_one_or_none()

    async def create(self, user: TUser) -> TUser:
        self.session.add(user)
        await self.session.flush()
        return user
