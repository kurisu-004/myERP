"""用户-角色关联仓储."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TRole, TUser, TUserRole
from utils.time import utcnow


class UserRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, link: TUserRole) -> TUserRole:
        self.session.add(link)
        await self.session.flush()
        return link

    async def assign(self, user_id: int, role_id: int) -> TUserRole | None:
        existing = await self.get_link(user_id, role_id)
        if existing is not None:
            return existing
        link = TUserRole(user_id=user_id, role_id=role_id)
        self.session.add(link)
        await self.session.flush()
        return link

    async def revoke(self, user_id: int, role_id: int) -> None:
        link = await self.get_link(user_id, role_id)
        if link is None:
            return

        link.deleted_at = utcnow()
        await self.session.flush()

    async def replace(self, user_id: int, role_ids: list[int]) -> None:
        now = utcnow()
        existing = await self.list_links_by_user(user_id)
        existing_ids = {l.role_id for l in existing}
        new_ids = set(role_ids)
        for link in existing:
            if link.role_id not in new_ids and link.deleted_at is None:
                link.deleted_at = now
        for rid in new_ids - existing_ids:
            self.session.add(TUserRole(user_id=user_id, role_id=rid))
        await self.session.flush()

    async def get_link(
        self, user_id: int, role_id: int, include_deleted: bool = False
    ) -> TUserRole | None:
        stmt = select(TUserRole).where(
            TUserRole.user_id == user_id,
            TUserRole.role_id == role_id,
        )
        if not include_deleted:
            stmt = stmt.where(TUserRole.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_links_by_user(
        self, user_id: int, include_deleted: bool = False
    ) -> list[TUserRole]:
        stmt = select(TUserRole).where(TUserRole.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(TUserRole.deleted_at.is_(None))
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_by_user(self, user_id: int) -> list[TRole]:
        stmt = (
            select(TRole)
            .join(TUserRole, TRole.id == TUserRole.role_id)
            .where(
                TUserRole.user_id == user_id,
                TUserRole.deleted_at.is_(None),
                TRole.deleted_at.is_(None),
            )
            .order_by(TRole.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_users_by_role(self, role_id: int) -> list[TUser]:
        stmt = (
            select(TUser)
            .join(TUserRole, TUser.id == TUserRole.user_id)
            .where(
                TUserRole.role_id == role_id,
                TUserRole.deleted_at.is_(None),
                TUser.deleted_at.is_(None),
            )
            .order_by(TUser.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())
