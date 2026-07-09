"""AuthService：通用登录 + JWT 签发。

登录后的 JWT payload 包含：
    sub        : user.id
    username   : user.username
    roles      : ["MANAGER", ...]
    shelf_ids  : [int, ...]    该 SHELF_ACCOUNT 账号能操作的 t_shelf.id 列表
    iat / exp / iss

注意：登录只允许 SHELF_ACCOUNT 角色绑定的 `t_shelf.is_active=True` 且未软删——
否则该 shelf_id 不会进 JWT。

登录响应 / `/auth/me` 的 user 部分会附带 `menus`（按 roles 在 t_role_menu
里筛，按 t_menu 排序组树），供前端侧边栏渲染。
"""
from __future__ import annotations

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError

from core.security import create_access_token, verify_password
from model.enums import ShelfZone, UserRole
from model.user_role import TUserRole
from repository.menu import MenuRepository
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from schema.user import CurrentUserOut, LoginRequest, LoginResponse
from service.menu import build_menu_tree


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        user_roles: UserRoleRepository,
        shelves: ShelfRepository,
        menus: MenuRepository,
    ) -> None:
        self.users = users
        self.user_roles = user_roles
        self.shelves = shelves
        self.menus = menus

    async def login(self, data: LoginRequest) -> LoginResponse:
        # username 保持原大小写，存库时存 lower（UserService.create_user 做）；
        # 登录时按存储形式（lower）查，避免混合大小写带来的混淆。
        username = data.username.strip().lower()
        u = await self.users.get_by_username(username)
        if u is None or u.deleted_at is not None or not u.is_active:
            # 不区分账号不存在 / 密码错，统一模糊信息防枚举
            raise BizError(
                code=ErrCode.BIZ_AUTH_INVALID,
                message="invalid username or password",
                http_status=http_status.HTTP_401_UNAUTHORIZED,
            )
        if not verify_password(data.password, u.password_hash):
            raise BizError(
                code=ErrCode.BIZ_AUTH_INVALID,
                message="invalid username or password",
                http_status=http_status.HTTP_401_UNAUTHORIZED,
            )

        roles = await self.user_roles.list_by_user(u.id)
        if not roles:
            raise BizError(
                code=ErrCode.BIZ_USER_NO_ROLE,
                message="account has no role assigned",
                http_status=http_status.HTTP_403_FORBIDDEN,
            )

        role_values = [r.role for r in roles]
        shelf_ids = await self._resolve_active_shelf_ids(roles)
        shelf_wildcard = self._has_wildcard_shelf_account(roles)
        menus_tree = await build_menu_tree(self.menus, role_values)

        token = create_access_token(
            user_id=u.id,
            username=u.username,
            roles=role_values,
            shelf_ids=shelf_ids,
            extra={"shelf_wildcard": shelf_wildcard},
        )
        # 更新最近登录时间
        await self.users.touch_login(u)

        return LoginResponse(
            token=token,
            user=CurrentUserOut(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                is_active=u.is_active,
                roles=role_values,
                shelf_ids=shelf_ids,  # type: ignore[arg-type]
                menus=menus_tree,
            ),
        )

    async def me(self, *, user_id: int, roles: list[str], shelf_ids: list[int]) -> CurrentUserOut:
        u = await self.users.get_by_id(user_id)
        if u is None or u.deleted_at is not None or not u.is_active:
            raise BizError(
                code=ErrCode.BIZ_AUTH_INVALID,
                message="user no longer active",
                http_status=http_status.HTTP_401_UNAUTHORIZED,
            )
        menus_tree = await build_menu_tree(self.menus, list(roles))
        return CurrentUserOut(
            id=u.id,
            username=u.username,
            full_name=u.full_name,
            is_active=u.is_active,
            roles=roles,
            shelf_ids=shelf_ids,  # type: ignore[arg-type]
            menus=menus_tree,
        )

    async def _resolve_active_shelf_ids(self, roles: list[TUserRole]) -> list[int]:
        """SHELF_ACCOUNT 角色所有 scope_id，且对应 shelf 仍 is_active / 未软删。"""
        ids: list[int] = []
        for r in roles:
            if r.role != UserRole.SHELF_ACCOUNT.value:
                continue
            if r.scope_type != "shelf" or r.scope_id is None:
                continue
            shelf = await self.shelves.get_by_id(int(r.scope_id))
            if shelf is None or shelf.deleted_at is not None or not shelf.is_active:
                continue
            if shelf.zone not in (
                ShelfZone.PRODUCTION.value,
                ShelfZone.INSPECTION.value,
            ):
                continue
            ids.append(int(shelf.id))
        return ids

    @staticmethod
    def _has_wildcard_shelf_account(roles: list[TUserRole]) -> bool:
        """共享 HMI 场景：账号有任一 SHELF_ACCOUNT 行 scope_id IS NULL
        → 该账号意图覆盖车间所有 PRODUCTION 货架（不绑死单架）。

        写入 JWT payload `shelf_wildcard` 字段，permission 层
        `CurrentUser.can_operate_shelf` 据此放行任意 shelf_id。
        """
        return any(
            r.role == UserRole.SHELF_ACCOUNT.value
            and r.scope_type == "shelf"
            and r.scope_id is None
            for r in roles
        )
