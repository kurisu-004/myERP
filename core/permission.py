"""当前用户 + 权限依赖工厂。

`get_current_user` 解 Bearer token，校验账号仍存在且 is_active，装配
`CurrentUser`。roles / shelf_ids 从 JWT 直接读，避免每个请求都查 DB。

权限通过两个工厂依赖授予：
- `require_role(UserRole.X)`         要求账号有指定角色
- `require_shelf_account(...)`       要求 SHELF_ACCOUNT 角色且能操作指定 shelf
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Depends, Request, status as http_status
from sqlalchemy import select

from core.database import SessionLocal
from core.error_code import ErrCode
from core.exception import BizError
from core.security import decode_access_token
from model.enums import UserRole
from model.user import TUser


@dataclass(frozen=True)
class CurrentUser:
    """从 JWT + DB 解出的当前账号上下文。"""

    id: int
    username: str
    full_name: str
    is_active: bool
    roles: tuple[str, ...]
    shelf_ids: tuple[int, ...]

    def has_role(self, role: str | UserRole) -> bool:
        target = role.value if isinstance(role, UserRole) else role
        return target in self.roles

    def can_operate_shelf(self, shelf_id: int) -> bool:
        """SHELF_ACCOUNT @ 此 shelf 可操作；MANAGER 一律允许（admin 越权兜底）。"""
        if self.has_role(UserRole.MANAGER):
            return True
        if not self.has_role(UserRole.SHELF_ACCOUNT):
            return False
        return shelf_id in self.shelf_ids


def _extract_bearer(request: Request) -> str:
    """从 `Authorization: Bearer <token>` 头取 token；缺失/格式错抛 401。"""
    header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    if not header:
        raise BizError(
            code=ErrCode.BIZ_AUTH_INVALID,
            message="missing Authorization header",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        )
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise BizError(
            code=ErrCode.BIZ_AUTH_INVALID,
            message="invalid Authorization header",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        )
    return parts[1]


async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI 依赖：从请求头取 JWT 并组装 CurrentUser。

    为避免 `core.permission` ↔ `api.deps` 循环导入，这里直接 `SessionLocal()`
    对 `t_user` 跑一个 SELECT；后续若需在事务内使用 `CurrentUser`，请改
    在 endpoint 里 `Depends(get_session)` 自行处理。
    """
    token = _extract_bearer(request)
    payload = decode_access_token(token)

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError) as e:
        raise BizError(
            code=ErrCode.BIZ_AUTH_INVALID,
            message="malformed token subject",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        ) from e

    async with SessionLocal() as session:
        stmt = select(TUser).where(TUser.id == user_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None or user.deleted_at is not None or not user.is_active:
        raise BizError(
            code=ErrCode.BIZ_AUTH_INVALID,
            message="user no longer active",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        )

    roles = tuple(payload.get("roles") or ())
    shelf_ids = tuple(int(x) for x in (payload.get("shelf_ids") or ()))
    if not roles:
        raise BizError(
            code=ErrCode.BIZ_USER_NO_ROLE,
            message="account has no role",
            http_status=http_status.HTTP_403_FORBIDDEN,
        )

    return CurrentUser(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=roles,
        shelf_ids=shelf_ids,
    )


def require_role(role: UserRole) -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：要求账号有指定 role。"""

    async def _dep(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if not user.has_role(role):
            raise BizError(
                code=ErrCode.FORBIDDEN,
                message=f"role {role.value} required",
                http_status=http_status.HTTP_403_FORBIDDEN,
            )
        return user

    return _dep


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：要求账号命中任一 role。

    业务场景：CLERK 与 MANAGER 都能下单/下发；CNC_PROGRAMMER 与 MANAGER 都能下发到
    CNC 货架，等等。带多种角色读权限的端点统一用这个工厂。
    """
    if not roles:
        raise ValueError("require_roles() needs at least one role")

    async def _dep(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if not any(user.has_role(r) for r in roles):
            expected = ", ".join(r.value for r in roles)
            raise BizError(
                code=ErrCode.FORBIDDEN,
                message=f"one of roles [{expected}] required",
                http_status=http_status.HTTP_403_FORBIDDEN,
            )
        return user

    return _dep


def require_auth() -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：仅要求已登录（任意角色）。

    比 `require_role(MANAGER)` 宽——SHELF_ACCOUNT 也通过。
    当前用法：仪表盘 / 扫描流程的读端点。
    """
    return get_current_user


def require_shelf_account_from_body(
    field: str,
) -> Callable[..., Awaitable[tuple[CurrentUser, int]]]:
    """依赖工厂：从请求 body 中取 shelf_id（int），校验可操作性。"""

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
        if shelf_id is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"missing or invalid '{field}' in body",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if not user.can_operate_shelf(shelf_id):
            raise BizError(
                code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                message="not authorized for this shelf",
                http_status=http_status.HTTP_403_FORBIDDEN,
            )
        return user, shelf_id

    return _dep


def require_part_file_role(
    kind: str | UserRole,
) -> Callable[..., Awaitable[CurrentUser]]:
    """依赖工厂：要求账号对该 kind 有写权限。

    映射见 `core._file_kind_policy.WRITE_ROLES_BY_KIND`：
    - DRAWING / 3D_MODEL / ASSEMBLY_MASTER → MANAGER + CLERK
    - G_CODE / SETUP_SHEET → MANAGER + CNC_PROGRAMMER

    用法（在 router 里）：
    ```python
    @router.post("/parts/{id}/drawings", dependencies=[
        Depends(require_part_file_role(PartFileKind.DRAWING))
    ])
    async def upload_drawing(...): ...
    ```
    """
    from core._file_kind_policy import WRITE_ROLES_BY_KIND
    from model.enums import PartFileKind

    kind_enum = PartFileKind(kind.value if isinstance(kind, UserRole) else kind)
    allowed = WRITE_ROLES_BY_KIND[kind_enum]

    async def _dep(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if not any(user.has_role(r) for r in allowed):
            expected = ", ".join(r.value for r in allowed)
            raise BizError(
                code=ErrCode.FORBIDDEN,
                message=(
                    f"kind={kind_enum.value} 写权限需要任一 role [{expected}]"
                ),
                http_status=http_status.HTTP_403_FORBIDDEN,
            )
        return user

    return _dep
