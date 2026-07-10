"""JWT 双 token 自动刷新 — 集成测试。

走真实 PostgreSQL（tests/conftest.py 起的 postgres-test 容器），覆盖：
- login 拿到 (access_token, refresh_token) 对
- 用合法 refresh_token 换新对 → DB t_user.refresh_token_version +1
- 旧 refresh_token 二次使用 → BIZ_AUTH_REFRESH_INVALID（轮转生效）
- user 被停用后 refresh → BIZ_AUTH_REFRESH_INVALID
- access token 当 refresh token 用 → BIZ_AUTH_REFRESH_INVALID

不走 HTTP TestClient（项目无此约定）；直接调 AuthService.refresh()。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
)
from model.user import TUser
from model.user_role import TUserRole
from model.enums import UserRole
from repository.menu import MenuRepository
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from schema.user import LoginRequest
from service.auth import AuthService
from core.exception import BizError
from core.error_code import ErrCode

pytestmark = pytest.mark.asyncio


# ======================================================================
# Fixtures
# ======================================================================


async def _make_user(session, *, username: str, password: str) -> TUser:
    """插入一个真实 t_user 行（含 password_hash）。"""
    u = TUser(
        username=username,
        password_hash=hash_password(password),
        full_name=f"测试 {username}",
        is_active=True,
        refresh_token_version=0,
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    session.add(u)
    await session.flush()
    return u


async def _make_role(session, *, user_id: int, role: str) -> TUserRole:
    r = TUserRole(
        user_id=user_id,
        role=role,
        scope_type=None,
        scope_id=None,
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    session.add(r)
    await session.flush()
    return r


def _build_auth_service(session) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        user_roles=UserRoleRepository(session),
        shelves=ShelfRepository(session),
        menus=MenuRepository(session),
    )


# ======================================================================
# Tests
# ======================================================================


async def test_login_returns_token_pair(clean_db):
    """login → LoginResponse 同时含 access_token + refresh_token；DB version=0。"""
    session = clean_db
    u = await _make_user(session, username="alice", password="pw123")
    await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)

    svc = _build_auth_service(session)
    result = await svc.login(LoginRequest(username="alice", password="pw123"))

    assert result.token
    assert result.refresh_token
    # access / refresh 的 type 字段可独立解码
    assert decode_refresh_token(result.refresh_token)["type"] == "refresh"
    assert decode_refresh_token(result.refresh_token)["ver"] == 0


async def test_refresh_happy_path_rotates_version(clean_db):
    """合法 refresh → 新对；DB version +1。"""
    session = clean_db
    u = await _make_user(session, username="bob", password="pw123")
    await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)

    svc = _build_auth_service(session)
    first = await svc.login(LoginRequest(username="bob", password="pw123"))
    assert u.refresh_token_version == 0

    # 第二次 refresh
    second = await svc.refresh(first.refresh_token)

    # 新 refresh token 一定不同（payload 里的 ver 从 0 → 1）；access token
    # 在同秒内 iat/exp 可能相同（签名一致），不强求。
    assert second.refresh_token
    assert second.refresh_token != first.refresh_token

    # DB version 自增
    assert u.refresh_token_version == 1


async def test_refresh_rejects_replayed_token(clean_db):
    """同一 refresh_token 用第二次 → 第二次失败（轮转生效）。"""
    session = clean_db
    u = await _make_user(session, username="carol", password="pw123")
    await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)

    svc = _build_auth_service(session)
    first = await svc.login(LoginRequest(username="carol", password="pw123"))

    # 第一次 refresh 成功
    await svc.refresh(first.refresh_token)
    # 此时 DB version=1，old refresh_token 的 ver=0（落后） → 第二次拒绝
    with pytest.raises(BizError) as exc_info:
        await svc.refresh(first.refresh_token)
    assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID
    assert "version mismatch" in exc_info.value.message.lower()


async def test_refresh_rejects_access_token(clean_db):
    """把 access token 喂给 refresh → type 不匹配 → 401。"""
    session = clean_db
    u = await _make_user(session, username="dave", password="pw123")
    await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)

    svc = _build_auth_service(session)
    first = await svc.login(LoginRequest(username="dave", password="pw123"))

    # access_token 当 refresh_token 用
    with pytest.raises(BizError) as exc_info:
        await svc.refresh(first.token)
    assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID


async def test_refresh_rejects_inactive_user(clean_db):
    """用户在 access 期间被停用 → refresh 拒绝。"""
    session = clean_db
    u = await _make_user(session, username="erin", password="pw123")
    await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)

    svc = _build_auth_service(session)
    first = await svc.login(LoginRequest(username="erin", password="pw123"))

    # 模拟运营把账号停用
    u.is_active = False
    await session.flush()

    with pytest.raises(BizError) as exc_info:
        await svc.refresh(first.refresh_token)
    assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID


async def test_refresh_legacy_access_token_without_type(clean_db):
    """手动签发无 type 字段的老 access token → 不能蒙混进 refresh。"""
    session = clean_db
    u = await _make_user(session, username="frank", password="pw123")
    await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)

    # 直接调 jwt.encode 不走 create_access_token，模拟"老 token 无 type"
    import jwt
    from datetime import datetime, timedelta, timezone
    from core.config import settings

    now = datetime.now(tz=timezone.utc)
    legacy = jwt.encode(
        {
            "sub": str(u.id),
            "username": "frank",
            "roles": ["MANAGER"],
            "shelf_ids": [],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=60)).timestamp()),
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    svc = _build_auth_service(session)
    with pytest.raises(BizError) as exc_info:
        await svc.refresh(legacy)
    assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID


async def test_refresh_re_resolves_roles_after_demotion(clean_db):
    """refresh 时从 DB 重读 roles —— 用户在 access 期间被降级时新 token 反映最新状态。"""
    session = clean_db
    u = await _make_user(session, username="grace", password="pw123")
    role_mgr = await _make_role(session, user_id=u.id, role=UserRole.MANAGER.value)
    await _make_role(session, user_id=u.id, role=UserRole.CLERK.value)

    svc = _build_auth_service(session)
    first = await svc.login(LoginRequest(username="grace", password="pw123"))
    assert "MANAGER" in first.user.roles
    assert "CLERK" in first.user.roles

    # 模拟运营把 MANAGER 角色移除
    role_mgr.soft_deleted = None  # 占位：实际通过软删实现
    await session.flush()
    # 显式软删：直接 UPDATE deleted_at
    from sqlalchemy import update
    await session.execute(
        update(TUserRole).where(TUserRole.id == role_mgr.id).values(deleted_at=datetime(2025, 1, 1))
    )
    await session.flush()

    # refresh
    second = await svc.refresh(first.refresh_token)
    # 新 token 的 roles 不应再含 MANAGER（已被降级），但仍有 CLERK
    assert "MANAGER" not in second.user.roles
    assert "CLERK" in second.user.roles
