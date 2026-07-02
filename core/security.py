"""密码哈希 + JWT 编解码。

- 密码：直接用 `bcrypt` 库（passlib 在新版 bcrypt 上的兼容探测有 bug，
  且 `cryptcontext` 不会带来额外价值——本项目固定 bcrypt 单种方案）。
- JWT：HS256 默认；payload 含 sub=用户 id、username、roles[]、shelf_ids[]。

错误处理：decode 失败一律抛 `BizError(BIZ_AUTH_INVALID, 401)`；
过期单独抛 `BizError(BIZ_AUTH_TOKEN_EXPIRED, 401)`。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import status as http_status

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError


def hash_password(plain: str) -> str:
    """bcrypt 哈希。rounds 越高越慢；通过 settings 调整（默认 12）。"""
    rounds = getattr(settings, "bcrypt_rounds", None) or 12
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode(
        "utf-8"
    )


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文与哈希是否匹配。"""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    *,
    user_id: int,
    username: str,
    roles: list[str],
    shelf_ids: list[int] | None = None,
    extra: dict | None = None,
) -> str:
    """签发 JWT。"""
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict = {
        "sub": str(user_id),
        "username": username,
        "roles": list(roles),
        "shelf_ids": list(shelf_ids or []),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.jwt_issuer,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """解 JWT；校验 exp / iss / sig。失败抛 BizError(401)。"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise BizError(
            code=ErrCode.BIZ_AUTH_TOKEN_EXPIRED,
            message="token expired",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        ) from e
    except jwt.InvalidTokenError as e:
        raise BizError(
            code=ErrCode.BIZ_AUTH_INVALID,
            message="invalid token",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        ) from e
    return payload
