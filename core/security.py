"""JWT 工具."""
from datetime import timedelta
from typing import Any

import jwt

from core.config import settings
from utils.time import utcnow


def create_access_token(
    subject: str | int,
    extra: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    now = utcnow()
    exp = now + timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """解码 JWT, 失败抛 jwt.PyJWTError."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
