"""权限点仓储."""
from datetime import datetime
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPermission


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, permission: TPermission) -> TPermission:
        self.session.add(permission)
        await self.session.flush()
        return permission

    async def get_by_id(
        self, permission_id: int, include_deleted: bool = False
    ) -> TPermission | None:
        p = await self.session.get(TPermission, permission_id)
        if p is None:
            return None
        if not include_deleted and p.deleted_at is not None:
            return None
        return p

    async def get_by_code(
        self, code: str, include_deleted: bool = False
    ) -> TPermission | None:
        stmt = select(TPermission).where(TPermission.code == code)
        if not include_deleted:
            stmt = stmt.where(TPermission.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, include_disabled: bool = False) -> list[TPermission]:
        stmt = select(TPermission).where(TPermission.deleted_at.is_(None))
        if not include_disabled:
            stmt = stmt.where(TPermission.is_enabled == 1)
        stmt = stmt.order_by(TPermission.type, TPermission.sort_order, TPermission.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_by_type(
        self, type: str, include_disabled: bool = False
    ) -> list[TPermission]:
        stmt = select(TPermission).where(
            TPermission.type == type,
            TPermission.deleted_at.is_(None),
        )
        if not include_disabled:
            stmt = stmt.where(TPermission.is_enabled == 1)
        stmt = stmt.order_by(TPermission.sort_order, TPermission.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_children(
        self, parent_id: int, include_disabled: bool = False
    ) -> list[TPermission]:
        stmt = select(TPermission).where(
            TPermission.parent_id == parent_id,
            TPermission.deleted_at.is_(None),
        )
        if not include_disabled:
            stmt = stmt.where(TPermission.is_enabled == 1)
        stmt = stmt.order_by(TPermission.sort_order, TPermission.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(self, permission: TPermission) -> TPermission:
        await self.session.flush()
        return permission

    async def soft_delete(self, permission: TPermission) -> None:
        permission.deleted_at = _utcnow_naive()
        await self.session.flush()

    async def list_enabled_codes_by_user(self, user_id: int) -> list[str]:
        from model import TRole, TRolePermission, TUserRole

        stmt = (
            select(TPermission.code)
            .join(TRolePermission, TPermission.id == TRolePermission.permission_id)
            .join(TRole, TRolePermission.role_id == TRole.id)
            .join(TUserRole, TRole.id == TUserRole.role_id)
            .where(
                TUserRole.user_id == user_id,
                TUserRole.deleted_at.is_(None),
                TRolePermission.deleted_at.is_(None),
                TPermission.deleted_at.is_(None),
                TRole.deleted_at.is_(None),
                TPermission.is_enabled == 1,
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)
