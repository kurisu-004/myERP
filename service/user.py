"""UserService：账号 CRUD + 角色管理。

- `create_user` 默认不分配角色，MANAGER 创建后单独 `add_role`。
- `add_role` SHELF_ACCOUNT 时强校验 scope_type='shelf' 且 scope_id 对应
  shelf 存在、is_active。
- 唯一冲突由 `IntegrityError` 翻成 `BIZ_USER_*`。
"""
from __future__ import annotations

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from core.security import hash_password, verify_password
from model import TShelf, TUser, TUserRole
from model.enums import ShelfZone, UserRole
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from schema.user import (
    UserCreateRequest,
    UserListOut,
    UserListQuery,
    UserOut,
    UserRoleOut,
    UserUpdateRequest,
)
from utils.id_gen import new_id


# 管理员重置密码时写入的默认口令。
DEFAULT_RESET_PASSWORD = "changeme"


class UserService:
    def __init__(
        self,
        users: UserRepository,
        user_roles: UserRoleRepository,
        shelves: ShelfRepository,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.users = users
        self.user_roles = user_roles
        self.shelves = shelves
        self._user_id: int | None = current_user.id if current_user else None

    # ============================================================
    # 列表
    # ============================================================
    async def list_users(self, query: UserListQuery) -> UserListOut:
        rows = await self.users.list_with_filters(
            username_like=query.username_like,
            is_active=query.is_active,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.users.count_with_filters(
            username_like=query.username_like,
            is_active=query.is_active,
        )
        items = [await self._to_out(u) for u in rows]
        return UserListOut(
            items=items, total=total, limit=query.limit, offset=query.offset
        )

    async def get_user(self, user_id: int) -> UserOut:
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return await self._to_out(u)

    # ============================================================
    # 创建 / 更新 / 软删
    # ============================================================
    async def create_user(self, data: UserCreateRequest) -> UserOut:
        username = data.username.strip().lower()
        existing = await self.users.get_by_username(username)
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_USER_DUPLICATE_USERNAME,
                message=f"username {username!r} already exists",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        u = TUser(
            id=new_id(),
            username=username,
            password_hash=hash_password(data.password),
            full_name=data.full_name.strip(),
            phone=(data.phone or "").strip() or None,
            is_active=True,
        )
        u.created_by = self._user_id
        u.updated_by = self._user_id
        await self.users.create(u)
        return await self._to_out(u)

    async def update_user(
        self, user_id: int, data: UserUpdateRequest
    ) -> UserOut:
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.full_name is not None:
            u.full_name = data.full_name.strip()
        if data.phone is not None:
            u.phone = data.phone.strip() or None
        if data.password is not None:
            u.password_hash = hash_password(data.password)
        if data.is_active is not None:
            u.is_active = data.is_active
        u.updated_by = self._user_id
        await self.users.update(u)
        # flush 后 onupdate=func.now() 会让 updated_at 过期；显式 refresh 在 async
        # 上下文里回填，避免 _to_out 同步读 updated_at 触发 MissingGreenlet。
        await self.users.session.refresh(u)
        return await self._to_out(u)

    async def soft_delete_user(self, user_id: int) -> UserOut:
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        u.updated_by = self._user_id
        await self.users.soft_delete(u)
        # flush 后 onupdate=func.now() 会让 updated_at 过期；显式 refresh 在 async
        # 上下文里回填，避免 _to_out 同步读 updated_at 触发 MissingGreenlet。
        await self.users.session.refresh(u)
        return await self._to_out(u, include_deleted=True)

    # ============================================================
    # 密码
    # ============================================================
    async def change_own_password(
        self, *, user_id: int, old_password: str, new_password: str
    ) -> None:
        """用户修改自己的密码：校验旧密码，写新哈希，并轮转 refresh token
        （让其他设备的旧 refresh token 立即失效）。

        密码哈希与 refresh_token_version 在同一次 UPDATE 内写完（单 flush），
        避免「先 update 再 increment」两阶段写触发 onupdate=updated_at 过期后
        的 lazy load → MissingGreenlet（见 CLAUDE.md item 13/15）。
        """
        u = await self.users.get_by_id(user_id)
        if u is None or u.deleted_at is not None or not u.is_active:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not verify_password(old_password, u.password_hash):
            raise BizError(
                code=ErrCode.BIZ_AUTH_OLD_PASSWORD_MISMATCH,
                message="旧密码不正确",
                http_status=http_status.HTTP_401_UNAUTHORIZED,
            )
        u.password_hash = hash_password(new_password)
        u.updated_by = self._user_id
        u.refresh_token_version = int(u.refresh_token_version) + 1  # 轮转，让旧 refresh 失效
        await self.users.update(u)

    async def admin_reset_password(self, user_id: int) -> UserOut:
        """管理员把指定账号密码重置为默认口令 changeme，并轮转 refresh token。

        密码哈希与 refresh_token_version 在同一次 UPDATE 内写完（单 flush），
        避免两阶段写触发 MissingGreenlet（见 CLAUDE.md item 13/15）。
        """
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        u.password_hash = hash_password(DEFAULT_RESET_PASSWORD)
        u.updated_by = self._user_id
        u.refresh_token_version = int(u.refresh_token_version) + 1  # 轮转，让旧 refresh 失效
        await self.users.update(u)
        # flush 后 onupdate=func.now() 会让 updated_at 过期；显式 refresh 在 async
        # 上下文里回填，避免 _to_out 同步读 updated_at 触发 MissingGreenlet。
        await self.users.session.refresh(u)
        return await self._to_out(u)

    # ============================================================
    # 角色管理
    # ============================================================
    async def list_user_roles(self, user_id: int) -> list[UserRoleOut]:
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        rows = await self.user_roles.list_by_user(u.id)
        shelf_ids = [r.scope_id for r in rows if r.scope_id]
        shelf_map = await self._shelf_map(shelf_ids)
        return [
            UserRoleOut(
                id=r.id,
                role=r.role,
                scope_type=r.scope_type,
                scope_id=r.scope_id,
                shelf_code=(shelf_map[r.scope_id].code if r.scope_id in shelf_map else None),
                shelf_name=(shelf_map[r.scope_id].name if r.scope_id in shelf_map else None),
            )
            for r in rows
        ]

    async def add_role(
        self,
        user_id: int,
        role: UserRole,
        scope_type: str | None,
        scope_id: int | None,
    ) -> UserRoleOut:
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        await self._validate_role_scope(role, scope_type, scope_id)

        r = TUserRole(
            id=new_id(),
            user_id=u.id,
            role=role.value,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        try:
            await self.user_roles.create(r)
        except Exception as e:
            # IntegrityError 视为重复（唯一约束冲突）
            msg = str(e).lower()
            if "unique" in msg or "duplicate" in msg or "uk_t_user_role" in msg:
                raise BizError(
                    code=ErrCode.BIZ_USER_ROLE_DUPLICATE,
                    message=(
                        f"role {role.value} (scope={scope_type}/{scope_id}) "
                        "already assigned to this user"
                    ),
                    http_status=http_status.HTTP_409_CONFLICT,
                ) from e
            raise

        shelf_map: dict[int, TShelf] = {}
        if role == UserRole.SHELF_ACCOUNT and scope_id is not None:
            shelf_map = await self._shelf_map([scope_id])
        return UserRoleOut(
            id=r.id,
            role=r.role,
            scope_type=r.scope_type,
            scope_id=r.scope_id,
            shelf_code=(shelf_map[scope_id].code if scope_id in shelf_map else None) if shelf_map else None,
            shelf_name=(shelf_map[scope_id].name if scope_id in shelf_map else None) if shelf_map else None,
        )

    async def remove_role(self, user_id: int, role_id: int) -> None:
        u = await self.users.get_by_id(user_id)
        if u is None:
            raise BizError(
                code=ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        r = await self.user_roles.get_by_id(role_id)
        if r is None or r.user_id != u.id or r.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_USER_ROLE_NOT_FOUND,
                message=f"role {role_id} not found for user {user_id}",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # TUserRole 不继承 AuditMixin，不写 created_by/updated_by。
        await self.user_roles.soft_delete(r)

    # ============================================================
    # 内部
    # ============================================================
    async def _validate_role_scope(
        self,
        role: UserRole,
        scope_type: str | None,
        scope_id: int | None,
    ) -> None:
        if role == UserRole.SHELF_ACCOUNT:
            if scope_type != "shelf" or scope_id is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="SHELF_ACCOUNT role requires scope_type='shelf' and scope_id",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            shelf = await self.shelves.get_by_id(scope_id)
            if shelf is None or shelf.deleted_at is not None:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_NOT_FOUND,
                    message=f"shelf {scope_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if shelf.zone not in (ShelfZone.PRODUCTION.value, ShelfZone.INSPECTION.value):
                raise BizError(
                    code=ErrCode.BIZ_SHELF_NOT_FOUND,
                    message=f"shelf {scope_id} has invalid zone {shelf.zone!r}",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if not shelf.is_active:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_NOT_FOUND,
                    message=f"shelf {shelf.code!r} is inactive; cannot bind",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
        else:
            # MANAGER / CLERK / INSPECTOR 暂不接 scope
            if scope_type is not None or scope_id is not None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=f"role {role.value} does not accept scope",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

    async def _shelf_map(self, ids: list[int]) -> dict[int, TShelf]:
        if not ids:
            return {}
        rows = await self.shelves.list_by_ids([int(x) for x in ids if x])
        return {s.id: s for s in rows}

    async def _to_out(
        self, u: TUser, *, include_deleted: bool = False
    ) -> UserOut:
        roles = await self.user_roles.list_by_user(u.id)
        shelf_ids = [r.scope_id for r in roles if r.scope_id]
        shelf_map = await self._shelf_map(shelf_ids)
        role_outs = [
            UserRoleOut(
                id=r.id,
                role=r.role,
                scope_type=r.scope_type,
                scope_id=r.scope_id,
                shelf_code=(shelf_map[r.scope_id].code if r.scope_id in shelf_map else None),
                shelf_name=(shelf_map[r.scope_id].name if r.scope_id in shelf_map else None),
            )
            for r in roles
        ]
        return UserOut(
            id=u.id,
            username=u.username,
            full_name=u.full_name,
            phone=u.phone,
            is_active=u.is_active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            updated_at=u.updated_at,
            roles=role_outs,
        )
