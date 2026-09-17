"""当前用户 + 权限依赖工厂。

2026-09-17 重大变更：v1 业务路由下线 + JWT 完全 Bypass。

`get_current_user` 直接返回一个固定 default `CurrentUser`
（id=1, username="default", roles=("MANAGER",), shelf_wildcard=True），
不再解 Bearer token、不查 DB。`require_role` / `require_roles` /
`require_part_file_role` 内部也直接 `return user`，依赖 default 拥有的
MANAGER 角色放行所有现存 v1 router 的角色检查。

- `core.security.decode_access_token` / `decode_refresh_token` **保留原
  实现**：`AuthService.login` / `AuthService.refresh` 仍用它们签发双 token，
  但因为没有 v1 router 强制调用 `get_current_user`，登录接口本身不需要走
  bypass。
- `require_shelf_account_from_body` 的 `can_operate_shelf` 对 MANAGER 短路
  放行，无需改。
- STS 端口（`api/v1/sts.py`）裸开鉴权（参考 `/api/mcp/*` 模式），靠部署层
  nginx / 安全组隔离，不依赖此文件。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Depends, Request

from model.enums import UserRole


# 2026-09-17 新增：JWT bypass 后所有请求都填这个 default 当前用户。
# 选 MANAGER 是为了不破坏既有 v1 router 的角色守卫（CLERK / CNC 也只是
# 多了不必要的权限，不会引入反而招致攻击面）。
_DEFAULT_USER: "CurrentUser" = None  # type: ignore[assignment]


def _build_default_user() -> "CurrentUser":
    return CurrentUser(
        id=1,
        username="default",
        full_name="Default User",
        is_active=True,
        roles=("MANAGER",),
        shelf_ids=(),
        shelf_wildcard=True,
    )


@dataclass(frozen=True)
class CurrentUser:
    """JWT bypass 后所有请求都填充的 default 账号上下文。"""

    id: int
    username: str
    full_name: str
    is_active: bool
    roles: tuple[str, ...]
    shelf_ids: tuple[int, ...]
    # 2026-09-17 起 bypass 模式默认 True，所有 SHELF_ACCOUNT 操作放行。
    shelf_wildcard: bool = False

    def has_role(self, role: str | UserRole) -> bool:
        target = role.value if isinstance(role, UserRole) else role
        return target in self.roles

    def can_operate_shelf(self, shelf_id: int) -> bool:
        """SHELF_ACCOUNT @ 此 shelf 可操作；MANAGER 一律允许（admin 越权兜底）。

        bypass 模式下 default 拥有 MANAGER → 所有调用都放行。
        """
        if self.has_role(UserRole.MANAGER):
            return True
        if not self.has_role(UserRole.SHELF_ACCOUNT):
            return False
        if self.shelf_wildcard:
            return True
        return shelf_id in self.shelf_ids


async def get_current_user_from_access_token(token: str) -> CurrentUser:
    """共用：解 access JWT 字符串 + t_user → CurrentUser。

    2026-09-17 变更：v1 业务路由已下线，此函数**忽略** `token` 参数直接返回
    default CurrentUser；保留签名仅是为了不破坏 `print` 端点（`/files/{id}/
    content`）形参 path 的 `access_token=<JWT>` 兼容调用。

    `core.security.decode_access_token` / `decode_refresh_token` 在
    `AuthService.login` / `AuthService.refresh` 仍被直接调用，不受 bypass 影响。
    """
    return _get_default_user()


def _get_default_user() -> CurrentUser:
    """lazy 单例：避免模块导入期就构造 dataclass。"""
    global _DEFAULT_USER
    if _DEFAULT_USER is None:
        _DEFAULT_USER = _build_default_user()
    return _DEFAULT_USER


async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI 依赖：2026-09-17 起 bypass 直接返回 default CurrentUser。"""
    return _get_default_user()


def require_role(role: UserRole) -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：要求账号有指定 role。

    2026-09-17 bypass：内部直接 return user（default 已含 MANAGER，覆盖所有 role）。
    """

    async def _dep(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        return user

    return _dep


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：要求账号命中任一 role。

    2026-09-17 bypass：内部直接 return user。
    """
    if not roles:
        raise ValueError("require_roles() needs at least one role")

    async def _dep(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        return user

    return _dep


def require_auth() -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：仅要求已登录（任意角色）。2026-09-17 bypass：等同 get_current_user。"""
    return get_current_user


def require_shelf_account_from_body(
    field: str,
) -> Callable[..., Awaitable[tuple[CurrentUser, int]]]:
    """依赖工厂：从请求 body 中取 shelf_id（int），校验可操作性。

    2026-09-17 bypass：`can_operate_shelf` 对 MANAGER 短路放行 → 所有请求过。
    """

    async def _dep(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> tuple[CurrentUser, int]:
        # FastAPI 已经把 body 解析好放在 request._json —— 兼容 lazy 解析
        body = getattr(request, "_json", None)
        if body is None:
            try:
                body = await request.json()
            except Exception:
                body = {}
        raw = body.get(field) if isinstance(body, dict) else None
        try:
            shelf_id = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            shelf_id = None
        return user, shelf_id  # type: ignore[return-value]

    return _dep


def require_part_file_role(
    kind: str | UserRole,
) -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：要求账号对该 kind 有写权限。

    2026-09-17 bypass：内部直接 return user。
    """

    async def _dep(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        return user

    return _dep
