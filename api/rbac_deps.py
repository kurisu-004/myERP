"""RBAC 依赖项: 鉴权 + 权限校验 + 超管白名单."""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWTError

from api.deps import get_uow
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.permission_cache import permission_cache
from core.security import decode_access_token
from model import TUser
from repository.unit_of_work import UnitOfWork


SUPERADMIN_ONLY_MSG = "super admin permission required"


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": ErrCode.UNAUTHORIZED,
                "message": "missing bearer token",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


async def get_current_user(
    authorization: str | None = Header(default=None),
    uow: UnitOfWork = Depends(get_uow),
) -> TUser:
    """从 Authorization: Bearer <token> 解析出当前用户."""
    token = _extract_bearer(authorization)
    try:
        payload = decode_access_token(token)
    except PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": ErrCode.TOKEN_INVALID,
                "message": f"token invalid: {exc}",
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": ErrCode.TOKEN_INVALID, "message": "token missing sub"},
        )
    user = await uow.users.get_by_id(int(sub))
    if user is None or user.deleted_at is not None or user.is_active != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": ErrCode.UNAUTHORIZED, "message": "user not found or disabled"},
        )
    return user


def require_permission(*codes: str):
    """装饰器/依赖工厂: 要求当前用户拥有任一权限码.

    超管 (username == superadmin) 直接放行.
    """

    async def _checker(
        user: TUser = Depends(get_current_user),
        uow: UnitOfWork = Depends(get_uow),
    ) -> TUser:
        if user.username == settings.superadmin_username:
            return user
        codes_now = await _load_user_perm_codes(user.id, uow)
        if "*" in codes_now or any(c in codes_now for c in codes):
            return user
        raise BizError(
            code=ErrCode.PERMISSION_DENIED,
            message=f"permission denied: need one of {list(codes)}",
            http_status=status.HTTP_403_FORBIDDEN,
        )

    return _checker


async def _load_user_perm_codes(user_id: int, uow: UnitOfWork) -> set[str]:
    cached = await permission_cache.get(f"user_perms:{user_id}")
    if cached is not None:
        return set(cached)
    codes = await uow.permissions.list_enabled_codes_by_user(user_id)
    await permission_cache.set(f"user_perms:{user_id}", codes)
    return set(codes)


def require_superadmin() -> Callable[..., Awaitable[TUser]]:
    """要求当前用户必须是 superadmin (用于权限管理页的入口保护)."""

    async def _checker(
        user: TUser = Depends(get_current_user),
    ) -> TUser:
        if user.username != settings.superadmin_username:
            raise BizError(
                code=ErrCode.FORBIDDEN,
                message=SUPERADMIN_ONLY_MSG,
                http_status=status.HTTP_403_FORBIDDEN,
            )
        return user

    return _checker
