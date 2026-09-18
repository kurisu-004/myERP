"""密码哈希 + JWT 编解码。

- 密码：直接用 `bcrypt` 库（passlib 在新版 bcrypt 上的兼容探测有 bug，
  且 `cryptcontext` 不会带来额外价值——本项目固定 bcrypt 单种方案）。
- JWT：HS256 默认；payload 含 sub=用户 id、username、roles[]、shelf_ids[]。
- 2026-07-10 起支持双 token + 轮转（plan jwt-token-token-token-robust-sonnet.md）：
  - access token：短 TTL（默认 12h / 720 min），type="access"；
  - refresh token：长 TTL（默认 7d / 10080 min），type="refresh"，载荷只保留
    sub + ver（轮转版本号），用于换 access token；每次成功 refresh 后
    t_user.refresh_token_version +1 → 旧 refresh token 立即失效。

错误处理：decode 失败一律抛 `BizError(BIZ_AUTH_INVALID/BIZ_AUTH_REFRESH_INVALID, 401)`；
过期单独抛对应版本号。

2026-09-19 IAM 域迁出：本仓 `service/auth.py` / `service/user.py` /
`api/v1/auth.py` 已删除（账号 / 登录 / 改密等路由整体由 backend-rust v2
的 `/api/v2/iam/*` 承接）；但本模块**保留**——下游消费者仍依赖：

- `tests/test_delivery_note_print_api.py` — 调用 `create_access_token` /
  `create_refresh_token` / `hash_password` 构造测试 token，断言
  `GET /delivery-notes/{id}/print` 鉴权链路（含 `ErrCode.BIZ_AUTH_INVALID`
  / `ErrCode.BIZ_AUTH_TOKEN_EXPIRED`）
- `tests/unit/test_part_service_workflow.py` — 断言扫码流程中 SHELF_ACCOUNT
  操作货架不匹配时抛 `ErrCode.BIZ_AUTH_SHELF_MISMATCH`
- `core/permission.py` docstring — 历史设计说明，不实际 import

彻底下线本模块需先迁移上述两个测试到「不依赖真实 JWT 签发 / 解码」的
纯 mock 路径（参考 `test_part_*_filter.py` 系列直接 patch `get_current_user`
的 fixture 写法）。在迁移完成前，本模块保留原逻辑。
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


def _build_token_claims(
    *,
    user_id: int,
    username: str,
    roles: list[str],
    shelf_ids: list[int],
    extra: dict | None,
    expires_minutes: int,
    token_type: str,
    extra_claims: dict | None = None,
) -> dict:
    """统一构造 access / refresh token 的 claims。

    token_type 写入 payload.type 字段；refresh token 还会额外塞 ver 用于
    轮转校验。extra 与 extra_claims 都合并到 payload（前者兼容历史用法）。
    """
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(minutes=expires_minutes)
    payload: dict = {
        "sub": str(user_id),
        "username": username,
        "roles": list(roles),
        "shelf_ids": list(shelf_ids),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.jwt_issuer,
    }
    if extra:
        payload.update(extra)
    if extra_claims:
        payload.update(extra_claims)
    return payload


def create_access_token(
    *,
    user_id: int,
    username: str,
    roles: list[str],
    shelf_ids: list[int] | None = None,
    extra: dict | None = None,
) -> str:
    """签发 access token（短 TTL），type="access"。

    老调用点（service/auth.py::login、me 等）的 extra 形参语义保持不变。
    """
    claims = _build_token_claims(
        user_id=user_id,
        username=username,
        roles=roles,
        shelf_ids=shelf_ids or [],
        extra=extra,
        expires_minutes=settings.jwt_access_token_expire_minutes,
        token_type="access",
    )
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    *,
    user_id: int,
    username: str,
    roles: list[str],
    shelf_ids: list[int],
    refresh_token_version: int,
) -> str:
    """签发 refresh token（长 TTL），type="refresh"，载荷只保留 sub + ver。

    ver 字段用于轮转校验：service.refresh() 比对 token.ver 与
    t_user.refresh_token_version，不一致即视为旧 token（已被新 refresh 顶掉）。
    """
    claims = _build_token_claims(
        user_id=user_id,
        username=username,
        roles=roles,
        shelf_ids=shelf_ids,
        extra=None,
        expires_minutes=settings.jwt_refresh_token_expire_minutes,
        token_type="refresh",
        extra_claims={"ver": int(refresh_token_version)},
    )
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_token(token: str, *, expected_type: str) -> dict:
    """共用解码器：校验 exp / iss / sig / type。

    兼容策略：
    - expected_type == "access" 且 payload.type 缺省 → 视为合法 access
      token（兼容 2026-07-10 之前签发的老 token，无 type 字段）；
    - expected_type == "refresh" 严格要求 payload.type == "refresh"
      （不允许老 access token 蒙混过关换新对）。

    错误码：refresh 类型失败 → BIZ_AUTH_REFRESH_INVALID；access 类型失败
    → 沿用 BIZ_AUTH_INVALID / BIZ_AUTH_TOKEN_EXPIRED。
    """
    if expected_type == "refresh":
        err_invalid = ErrCode.BIZ_AUTH_REFRESH_INVALID
        err_expired = ErrCode.BIZ_AUTH_REFRESH_INVALID
        kind_label = "refresh"
    else:
        err_invalid = ErrCode.BIZ_AUTH_INVALID
        err_expired = ErrCode.BIZ_AUTH_TOKEN_EXPIRED
        kind_label = "access"

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
            code=err_expired,
            message=f"{kind_label} token expired",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        ) from e
    except jwt.InvalidTokenError as e:
        raise BizError(
            code=err_invalid,
            message=f"invalid {kind_label} token",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        ) from e

    actual_type = payload.get("type")
    if expected_type == "access" and actual_type is None:
        # 老 token 无 type 字段，视为 access
        return payload
    if actual_type != expected_type:
        raise BizError(
            code=err_invalid,
            message=f"token type mismatch (expected {expected_type})",
            http_status=http_status.HTTP_401_UNAUTHORIZED,
        )
    return payload


def decode_access_token(token: str) -> dict:
    """解 access token；老 token 无 type 字段时兼容通过。"""
    return _decode_token(token, expected_type="access")


def decode_refresh_token(token: str) -> dict:
    """解 refresh token；严格要求 type="refresh"。"""
    return _decode_token(token, expected_type="refresh")
