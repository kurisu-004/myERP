"""修改密码 / 管理员重置密码 — 集成测试（走真实 PostgreSQL）。

覆盖 UserService.change_own_password / admin_reset_password：
- 关键回归：单次 UPDATE（不走两阶段写），避免 onupdate=updated_at 过期后
  在 _to_out 里 lazy load 触发 MissingGreenlet（CLAUDE.md item 13/15）。
- 旧密码校验、默认口令 changeme、refresh_token_version 轮转。

直接调 UserService（项目无 HTTP TestClient 约定）。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from core.security import hash_password, verify_password
from model.user import TUser
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from service.user import UserService

pytestmark = pytest.mark.asyncio


async def _make_user(session, *, username: str, password: str) -> TUser:
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


def _build_user_service(session, *, actor_id: int | None = None) -> UserService:
    current = None
    if actor_id is not None:
        current = CurrentUser(
            id=actor_id,
            username="actor",
            full_name="Actor",
            is_active=True,
            roles=("MANAGER",),
            shelf_ids=(),
        )
    return UserService(
        users=UserRepository(session),
        user_roles=UserRoleRepository(session),
        shelves=ShelfRepository(session),
        current_user=current,
    )


async def test_change_own_password_success_no_missing_greenlet(clean_db):
    """旧密码正确 → 改密成功，版本 +1；单次 UPDATE 不触发 MissingGreenlet。"""
    session = clean_db
    u = await _make_user(session, username="alice", password="old_pw")

    svc = _build_user_service(session, actor_id=u.id)
    await svc.change_own_password(
        user_id=u.id, old_password="old_pw", new_password="new_secret"
    )

    # DB 里能读到新哈希 + 版本轮转（若两阶段写会在此前抛 MissingGreenlet）
    await session.refresh(u)
    assert verify_password("new_secret", u.password_hash)
    assert not verify_password("old_pw", u.password_hash)
    assert u.refresh_token_version == 1


async def test_change_own_password_wrong_old_401(clean_db):
    session = clean_db
    u = await _make_user(session, username="bob", password="old_pw")

    svc = _build_user_service(session, actor_id=u.id)
    with pytest.raises(BizError) as exc:
        await svc.change_own_password(
            user_id=u.id, old_password="WRONG", new_password="new_secret"
        )
    assert exc.value.code == ErrCode.BIZ_AUTH_OLD_PASSWORD_MISMATCH

    # 未改动
    await session.refresh(u)
    assert verify_password("old_pw", u.password_hash)
    assert u.refresh_token_version == 0


async def test_admin_reset_password_success_returns_out(clean_db):
    """管理员重置为 changeme + 版本轮转；_to_out 正常返回（回归 MissingGreenlet）。"""
    session = clean_db
    admin = await _make_user(session, username="mgr", password="mgrpw")
    target = await _make_user(session, username="carol", password="secret")

    svc = _build_user_service(session, actor_id=admin.id)
    out = await svc.admin_reset_password(target.id)

    # _to_out 读到了 updated_at（未抛 MissingGreenlet），且返回目标账号
    assert str(out.id) == str(target.id)
    assert out.username == "carol"

    await session.refresh(target)
    assert verify_password("changeme", target.password_hash)
    assert target.refresh_token_version == 1
    assert target.updated_by == admin.id


async def test_admin_reset_password_not_found_404(clean_db):
    session = clean_db
    svc = _build_user_service(session, actor_id=1)
    with pytest.raises(BizError) as exc:
        await svc.admin_reset_password(999999999)
    assert exc.value.code == ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND
