"""角色-权限关联仓储."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPermission, TRole, TRolePermission


class RolePermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, link: TRolePermission) -> TRolePermission:
        self.session.add(link)
        await self.session.flush()
        return link

    async def grant(self, role_id: int, permission_id: int) -> TRolePermission | None:
        existing = await self.get_link(role_id, permission_id)
        if existing is not None:
            return existing
        link = TRolePermission(role_id=role_id, permission_id=permission_id)
        self.session.add(link)
        await self.session.flush()
        return link

    async def revoke(self, role_id: int, permission_id: int) -> None:
        link = await self.get_link(role_id, permission_id)
        if link is None:
            return
        from datetime import datetime
        from datetime import timezone

        link.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()

    async def replace(self, role_id: int, permission_ids: list[int]) -> None:
        from datetime import datetime
        from datetime import timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        existing = await self.list_links_by_role(role_id)
        existing_ids = {l.permission_id for l in existing}
        new_ids = set(permission_ids)
        for link in existing:
            if link.permission_id not in new_ids and link.deleted_at is None:
                link.deleted_at = now
        for pid in new_ids - existing_ids:
            self.session.add(TRolePermission(role_id=role_id, permission_id=pid))
        await self.session.flush()

    async def get_link(
        self, role_id: int, permission_id: int, include_deleted: bool = False
    ) -> TRolePermission | None:
        stmt = select(TRolePermission).where(
            TRolePermission.role_id == role_id,
            TRolePermission.permission_id == permission_id,
        )
        if not include_deleted:
            stmt = stmt.where(TRolePermission.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_links_by_role(
        self, role_id: int, include_deleted: bool = False
    ) -> list[TRolePermission]:
        stmt = select(TRolePermission).where(TRolePermission.role_id == role_id)
        if not include_deleted:
            stmt = stmt.where(TRolePermission.deleted_at.is_(None))
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_permissions_by_role(self, role_id: int) -> list[TPermission]:
        stmt = (
            select(TPermission)
            .join(TRolePermission, TPermission.id == TRolePermission.permission_id)
            .where(
                TRolePermission.role_id == role_id,
                TRolePermission.deleted_at.is_(None),
                TPermission.deleted_at.is_(None),
            )
            .order_by(TPermission.sort_order, TPermission.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_codes_by_role(
        self, role_id: int, enabled_only: bool = True
    ) -> list[str]:
        stmt = (
            select(TPermission.code)
            .join(TRolePermission, TPermission.id == TRolePermission.permission_id)
            .where(
                TRolePermission.role_id == role_id,
                TRolePermission.deleted_at.is_(None),
                TPermission.deleted_at.is_(None),
            )
        )
        if enabled_only:
            stmt = stmt.where(TPermission.is_enabled == 1)
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)
