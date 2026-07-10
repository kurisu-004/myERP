from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TUser, TUserRole


class UserRepository:
    """t_user 数据访问。

    username 走小写比对；service 层负责。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, user: TUser) -> TUser:
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user: TUser) -> TUser:
        await self.session.flush()
        return user

    async def soft_delete(self, user: TUser) -> TUser:
        user.deleted_at = now_naive()
        user.is_active = False
        await self.session.flush()
        return user

    async def touch_login(self, user: TUser) -> TUser:
        user.last_login_at = now_naive()
        await self.session.flush()
        return user

    async def increment_refresh_token_version(self, user: TUser) -> TUser:
        """refresh token 轮转：版本 +1，让旧 refresh token 失效。

        写入后旧 refresh token 仍能解码 JWT 本身（exp 未到），但其携带的
        `ver` 字段落后于 DB 当前值 → service.refresh() 比对失败抛
        BIZ_AUTH_REFRESH_INVALID。
        """
        user.refresh_token_version = int(user.refresh_token_version) + 1
        await self.session.flush()
        return user

    # ===== 单条 =====
    async def get_by_id(
        self, user_id: int, *, include_deleted: bool = False
    ) -> TUser | None:
        result = await self.session.get(TUser, user_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_username(
        self, username_lower: str, *, include_deleted: bool = False
    ) -> TUser | None:
        """按 username 定位（service 层已 lower，DB 走 = 比对）。"""
        stmt = select(TUser).where(TUser.username == username_lower)
        if not include_deleted:
            stmt = stmt.where(TUser.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表 =====
    async def list_with_filters(
        self,
        *,
        username_like: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TUser]:
        stmt = select(TUser)
        if not include_deleted:
            stmt = stmt.where(TUser.deleted_at.is_(None))
        if username_like:
            stmt = stmt.where(TUser.username.ilike(f"%{username_like}%"))
        if is_active is not None:
            stmt = stmt.where(TUser.is_active.is_(is_active))
        stmt = stmt.order_by(TUser.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        username_like: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TUser)
        if not include_deleted:
            stmt = stmt.where(TUser.deleted_at.is_(None))
        if username_like:
            stmt = stmt.where(TUser.username.ilike(f"%{username_like}%"))
        if is_active is not None:
            stmt = stmt.where(TUser.is_active.is_(is_active))
        stmt = stmt.with_only_columns(func.count(TUser.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())


class UserRoleRepository:
    """t_user_role 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, role: TUserRole) -> TUserRole:
        self.session.add(role)
        await self.session.flush()
        return role

    async def soft_delete(self, role: TUserRole) -> TUserRole:
        role.deleted_at = now_naive()
        await self.session.flush()
        return role

    # ===== 单条 =====
    async def get_by_id(
        self, role_id: int, *, include_deleted: bool = False
    ) -> TUserRole | None:
        result = await self.session.get(TUserRole, role_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    # ===== 列表 =====
    async def list_by_user(
        self, user_id: int, *, role: str | None = None
    ) -> list[TUserRole]:
        stmt = select(TUserRole).where(
            TUserRole.user_id == user_id,
            TUserRole.deleted_at.is_(None),
        )
        if role:
            stmt = stmt.where(TUserRole.role == role)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_shelf_ids_for_user(self, user_id: int) -> list[int]:
        """SHELF_ACCOUNT 角色的所有 scope_id（用于 JWT 嵌入 + 登录校验）。"""
        stmt = select(TUserRole.scope_id).where(
            TUserRole.user_id == user_id,
            TUserRole.deleted_at.is_(None),
            TUserRole.role == "SHELF_ACCOUNT",
            TUserRole.scope_type == "shelf",
            TUserRole.scope_id.is_not(None),
        )
        result = await self.session.execute(stmt)
        return [int(x) for x in result.scalars().all()]
