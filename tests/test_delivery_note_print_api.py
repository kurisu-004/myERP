"""送货单 GET /print 端点鉴权测试。

2026-07-25 改为 GET 单路由（Authorization header 唯一鉴权方式）：
- 删除 POST form body / iframe / `?token=` URL 路径
- 后端 GET 路由 `_OFFICE_DEP = [Depends(require_roles(MANAGER, CLERK))]`
- 鉴权链路：`get_current_user(request)` 解 Bearer JWT → `require_roles(MANAGER, CLERK)` 校验角色

按项目约定：直接调 `core.permission.get_current_user(request)` + `require_roles(...)`
两个依赖，不经 HTTP TestClient（tests/test_delivery_note.py:3 约定）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.permission import get_current_user, require_roles
from core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
)
from model.enums import UserRole
from model.user import TUser
from model.user_role import TUserRole
from repository.menu import MenuRepository
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from service.auth import AuthService

pytestmark = pytest.mark.asyncio


# ============================================================
# Fixtures
# ============================================================


async def _make_user(
    session,
    *,
    username: str,
    roles: list[str],
    is_active: bool = True,
) -> TUser:
    u = TUser(
        username=username,
        password_hash=hash_password("pw123"),
        full_name=f"测试 {username}",
        is_active=is_active,
        refresh_token_version=0,
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    session.add(u)
    await session.flush()
    for r in roles:
        session.add(
            TUserRole(
                user_id=u.id, role=r, scope_type=None, scope_id=None,
                created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
            )
        )
    await session.flush()
    return u


def _build_auth_service(session) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        user_roles=UserRoleRepository(session),
        shelves=ShelfRepository(session),
        menus=MenuRepository(session),
    )


def _fake_request(*, authorization: str | None = None) -> SimpleNamespace:
    """最小 Request mock：`get_current_user` 只用 `request.headers.get("authorization")`。"""
    headers: dict[str, str] = {}
    if authorization is not None:
        headers["authorization"] = authorization
    return SimpleNamespace(headers=headers)


async def _commit(session) -> None:
    """`get_current_user` 内部用 SessionLocal() 开新 session；必须 commit 才能让新 session 看到数据。"""
    await session.commit()


# ============================================================
# get_current_user: Bearer header → CurrentUser
# ============================================================


async def test_header_manager_returns_current_user(clean_db):
    """GET /print + MANAGER + 合法 Authorization header → 返回 CurrentUser。"""
    from schema.user import LoginRequest

    u = await _make_user(clean_db, username="mgr", roles=[UserRole.MANAGER.value])
    await _commit(clean_db)
    svc = _build_auth_service(clean_db)
    pair = await svc.login(LoginRequest(username="mgr", password="pw123"))

    user = await get_current_user(
        request=_fake_request(authorization=f"Bearer {pair.token}"),
    )
    assert user.id == u.id
    assert UserRole.MANAGER.value in user.roles


async def test_header_clerk_returns_current_user(clean_db):
    """GET /print + CLERK + 合法 Authorization header → 返回 CurrentUser。"""
    from schema.user import LoginRequest

    u = await _make_user(clean_db, username="clk", roles=[UserRole.CLERK.value])
    await _commit(clean_db)
    svc = _build_auth_service(clean_db)
    pair = await svc.login(LoginRequest(username="clk", password="pw123"))

    user = await get_current_user(
        request=_fake_request(authorization=f"Bearer {pair.token}"),
    )
    assert user.id == u.id
    assert UserRole.CLERK.value in user.roles


async def test_missing_header_rejected_401(clean_db):
    """GET /print 无 Authorization header → 401。"""
    with pytest.raises(BizError) as exc_info:
        await get_current_user(request=_fake_request())
    assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
    assert exc_info.value.http_status == 401
    assert "missing Authorization header" in exc_info.value.message


async def test_refresh_token_in_header_rejected_401(clean_db):
    """GET /print + refresh token (type=refresh) → 401（type 不匹配 access）。"""
    rt = create_refresh_token(
        user_id=1, username="x",
        roles=[UserRole.MANAGER.value], shelf_ids=[],
        refresh_token_version=0,
    )
    with pytest.raises(BizError) as exc_info:
        await get_current_user(
            request=_fake_request(authorization=f"Bearer {rt}"),
        )
    assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
    assert exc_info.value.http_status == 401


async def test_garbage_token_in_header_rejected_401(clean_db):
    """GET /print + 乱字符串 Bearer → 401。"""
    with pytest.raises(BizError) as exc_info:
        await get_current_user(
            request=_fake_request(authorization="Bearer not-a-jwt"),
        )
    assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID


async def test_tampered_signature_rejected_401(clean_db):
    """GET /print + 签名被改 → 401。"""
    from schema.user import LoginRequest

    await _make_user(clean_db, username="mgr_tamper", roles=[UserRole.MANAGER.value])
    await _commit(clean_db)
    svc = _build_auth_service(clean_db)
    pair = await svc.login(LoginRequest(username="mgr_tamper", password="pw123"))
    tampered = pair.token[:-3] + "AAA"

    with pytest.raises(BizError) as exc_info:
        await get_current_user(
            request=_fake_request(authorization=f"Bearer {tampered}"),
        )
    assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID


async def test_wrong_issuer_rejected_401(clean_db):
    """GET /print + 错误 issuer → 401。"""
    await _make_user(clean_db, username="mgr_iss", roles=[UserRole.MANAGER.value])
    await _commit(clean_db)
    now = datetime.now(tz=timezone.utc)
    bad = jwt.encode(
        {
            "sub": "1", "username": "mgr_iss",
            "roles": [UserRole.MANAGER.value], "shelf_ids": [],
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=60)).timestamp()),
            "iss": "evil-issuer",
        },
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(BizError) as exc_info:
        await get_current_user(
            request=_fake_request(authorization=f"Bearer {bad}"),
        )
    assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID


async def test_expired_access_token_rejected_401(clean_db):
    """GET /print + 过期 access token → 401 BIZ_AUTH_TOKEN_EXPIRED。"""
    await _make_user(clean_db, username="mgr_exp", roles=[UserRole.MANAGER.value])
    await _commit(clean_db)
    now = datetime.now(tz=timezone.utc)
    expired = jwt.encode(
        {
            "sub": "1", "username": "mgr_exp",
            "roles": [UserRole.MANAGER.value], "shelf_ids": [],
            "type": "access",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(BizError) as exc_info:
        await get_current_user(
            request=_fake_request(authorization=f"Bearer {expired}"),
        )
    assert exc_info.value.code == ErrCode.BIZ_AUTH_TOKEN_EXPIRED


async def test_inactive_user_rejected_401(clean_db):
    """GET /print + 停用账号 → 401（login 后被 deactivate 的竞态）。"""
    await _make_user(
        clean_db, username="ghost", roles=[UserRole.MANAGER.value], is_active=False,
    )
    await _commit(clean_db)
    fresh = create_access_token(
        user_id=1, username="ghost",
        roles=[UserRole.MANAGER.value], shelf_ids=[],
    )
    with pytest.raises(BizError) as exc_info:
        await get_current_user(
            request=_fake_request(authorization=f"Bearer {fresh}"),
        )
    assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
    assert exc_info.value.http_status == 401


# ============================================================
# require_roles(MANAGER, CLERK): GET /print 的 _OFFICE_DEP
# ============================================================


async def test_office_role_required_rejects_shelf_account(clean_db):
    """GET /print 的 _OFFICE_DEP 要求 MANAGER / CLERK；SHELF_ACCOUNT → 403。"""
    from core.permission import CurrentUser

    fake_user = CurrentUser(
        id=1, username="sh", full_name="", is_active=True,
        roles=(UserRole.SHELF_ACCOUNT.value,), shelf_ids=(),
    )
    dep = require_roles(UserRole.MANAGER, UserRole.CLERK)
    with pytest.raises(BizError) as exc_info:
        await dep(user=fake_user)
    assert exc_info.value.code == ErrCode.FORBIDDEN
    assert exc_info.value.http_status == 403


async def test_office_role_required_accepts_manager():
    """GET /print + MANAGER 通过 _OFFICE_DEP。"""
    from core.permission import CurrentUser

    fake_user = CurrentUser(
        id=1, username="m", full_name="", is_active=True,
        roles=(UserRole.MANAGER.value,), shelf_ids=(),
    )
    dep = require_roles(UserRole.MANAGER, UserRole.CLERK)
    out = await dep(user=fake_user)
    assert out is fake_user


async def test_office_role_required_accepts_clerk():
    """GET /print + CLERK 通过 _OFFICE_DEP。"""
    from core.permission import CurrentUser

    fake_user = CurrentUser(
        id=1, username="c", full_name="", is_active=True,
        roles=(UserRole.CLERK.value,), shelf_ids=(),
    )
    dep = require_roles(UserRole.MANAGER, UserRole.CLERK)
    out = await dep(user=fake_user)
    assert out is fake_user


# ============================================================
# 端到端：登录 → get_current_user → _OFFICE_DEP → 拿到 note
# ============================================================


async def test_e2e_header_only_full_path_for_manager(clean_db):
    """MANAGER 走完整鉴权链路：login → get_current_user (Bearer header) → require_roles → 通过。"""
    from schema.user import LoginRequest

    u = await _make_user(clean_db, username="mgr_e2e", roles=[UserRole.MANAGER.value])
    await _commit(clean_db)
    svc = _build_auth_service(clean_db)
    pair = await svc.login(LoginRequest(username="mgr_e2e", password="pw123"))

    # 模拟 GET /print 端点的依赖链
    user = await get_current_user(
        request=_fake_request(authorization=f"Bearer {pair.token}"),
    )
    dep = require_roles(UserRole.MANAGER, UserRole.CLERK)
    final_user = await dep(user=user)

    assert final_user.id == u.id
    assert UserRole.MANAGER.value in final_user.roles