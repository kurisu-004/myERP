"""认证 + 权限加载服务.

- login(): 验证 username/password, 颁发 JWT
- load_user_permission_codes(): 查 user→role→permission 的所有启用 code
- load_user_menus(): 组装当前用户的菜单树 (MENU 类型 + parent_id 自引用)
- load_me(): 登录后 me 接口聚合信息
- is_superadmin(): 超管白名单判断
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.security import create_access_token
from model import TPermission, TUser
from repository.unit_of_work import UnitOfWork
from schema.rbac import (
    MenuNodeOut,
    MeOut,
    RoleBriefOut,
    TokenOut,
    UserOut,
)
from utils.password import verify_password


class AuthService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    # -------- superadmin 白名单 --------
    def is_superadmin(self, user: TUser) -> bool:
        return user.username == settings.superadmin_username

    # -------- 登录 --------
    async def login(self, username: str, password: str) -> TokenOut:
        user = await self.uow.users.get_by_username(
            username, include_deleted=True, include_inactive=True
        )
        if user is None:
            raise BizError(
                code=ErrCode.INVALID_CREDENTIALS,
                message="invalid username or password",
                http_status=401,
            )
        if user.deleted_at is not None or user.is_active != 1:
            raise BizError(
                code=ErrCode.BIZ_USER_DISABLED,
                message="user is disabled or deleted",
                http_status=403,
            )
        if not verify_password(password, user.password_hash):
            raise BizError(
                code=ErrCode.INVALID_CREDENTIALS,
                message="invalid username or password",
                http_status=401,
            )
        # 写入 last_login_at
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.uow.users.update_last_login(user.id, now)
        await self.uow.commit()

        token = create_access_token(
            subject=user.id, extra={"username": user.username}
        )
        return TokenOut(
            access_token=token,
            token_type="bearer",
            expires_in=settings.jwt_expire_minutes * 60,
        )

    # -------- 权限码 --------
    async def load_user_permission_codes(self, user_id: int) -> list[str]:
        user = await self.uow.users.get_by_id(user_id)
        if user is None:
            raise BizError(
                code=ErrCode.BIZ_USER_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=404,
            )
        if self.is_superadmin(user):
            return ["*"]
        codes = await self.uow.permissions.list_enabled_codes_by_user(user_id)
        # 保留稳定顺序
        seen: set[str] = set()
        ordered: list[str] = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        return ordered

    # -------- 菜单树 --------
    async def load_user_menus(self, user_id: int) -> list[MenuNodeOut]:
        from sqlalchemy import select

        from model import (
            TRole,
            TRolePermission,
            TUserRole,
        )

        user = await self.uow.users.get_by_id(user_id)
        if user is None:
            raise BizError(
                code=ErrCode.BIZ_USER_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=404,
            )
        if self.is_superadmin(user):
            return []  # 超管由前端全放行

        stmt = (
            select(TPermission)
            .join(TRolePermission, TPermission.id == TRolePermission.permission_id)
            .join(TRole, TRolePermission.role_id == TRole.id)
            .join(TUserRole, TRole.id == TUserRole.role_id)
            .where(
                TUserRole.user_id == user_id,
                TUserRole.deleted_at.is_(None),
                TRolePermission.deleted_at.is_(None),
                TPermission.deleted_at.is_(None),
                TRole.deleted_at.is_(None),
                TPermission.type == "MENU",
                TPermission.is_enabled == 1,
            )
            .order_by(TPermission.sort_order, TPermission.id)
        )
        rows = list((await self.uow.session.execute(stmt)).scalars().all())
        return _build_menu_tree(rows)

    # -------- me --------
    async def load_me(self, user_id: int) -> MeOut:
        user = await self.uow.users.get_by_id(user_id)
        if user is None:
            raise BizError(
                code=ErrCode.BIZ_USER_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=404,
            )
        roles = await self.uow.user_roles.list_by_user(user_id)
        if self.is_superadmin(user):
            return MeOut(
                user=UserOut.model_validate(user),
                roles=[],
                permissions=["*"],
                menus=[],
                is_superadmin=True,
            )
        codes = await self.load_user_permission_codes(user_id)
        menus = await self.load_user_menus(user_id)
        return MeOut(
            user=UserOut.model_validate(user),
            roles=[RoleBriefOut.model_validate(r) for r in roles],
            permissions=codes,
            menus=menus,
            is_superadmin=False,
        )


def _build_menu_tree(perms: list[TPermission]) -> list[MenuNodeOut]:
    """组装菜单树: 顶层 (parent_id IS NULL) → 子节点递归."""
    nodes: dict[int, MenuNodeOut] = {}
    for p in perms:
        nodes[p.id] = MenuNodeOut(
            id=p.id,
            code=p.code,
            name=p.name,
            path=p.path,
            icon=p.icon,
            component=p.component,
            sort_order=p.sort_order,
            children=[],
        )
    roots: list[MenuNodeOut] = []
    for p in perms:
        node = nodes[p.id]
        if p.parent_id is None or p.parent_id not in nodes:
            roots.append(node)
        else:
            nodes[p.parent_id].children.append(node)
    return roots
