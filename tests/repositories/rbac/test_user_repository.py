"""UserRepository 扩展: 软删除 + 按 username/employee_no 查 + active 过滤."""
import pytest

from model import TUser
from repository.user import UserRepository
from utils.password import hash_password


@pytest.mark.integration
async def test_create_user_with_required_fields(user_repo: UserRepository):
    u = await user_repo.create(
        TUser(
            id=10_000_000_001,
            employee_no="U0001",
            username="alice",
            name="Alice",
            password_hash=hash_password("p"),
        )
    )
    assert u.id == 10_000_000_001
    assert u.employee_no == "U0001"
    assert u.is_active == 1


@pytest.mark.integration
async def test_get_by_username_excludes_deleted(
    user_repo: UserRepository, user_factory
):
    u = await user_factory("ghost")
    await user_repo.soft_delete(u)
    assert await user_repo.get_by_username("ghost") is None
    assert await user_repo.get_by_username("ghost", include_deleted=True) is not None


@pytest.mark.integration
async def test_get_by_username_inactive_returns_none(
    user_repo: UserRepository, user_factory
):
    u = await user_factory("sleeper", is_active=0)
    assert await user_repo.get_by_username("sleeper") is None
    assert await user_repo.get_by_username("sleeper", include_inactive=True) is not None


@pytest.mark.integration
async def test_get_by_employee_no(user_repo: UserRepository, user_factory):
    await user_factory("bob", employee_no="U_BOB")
    u = await user_repo.get_by_employee_no("U_BOB")
    assert u is not None
    assert u.username == "bob"
    assert await user_repo.get_by_employee_no("nope") is None


@pytest.mark.integration
async def test_list_paginated_excludes_deleted(
    user_repo: UserRepository, user_factory
):
    u_keep = await user_factory("keep")
    u_del = await user_factory("del")
    await user_repo.soft_delete(u_del)
    items, total = await user_repo.list_paginated(page=1, size=50)
    assert total >= 1
    assert u_keep.id in {i.id for i in items}
    assert u_del.id not in {i.id for i in items}


@pytest.mark.integration
async def test_list_paginated_search(
    user_repo: UserRepository, user_factory
):
    await user_factory("alice")
    await user_factory("bob")
    await user_factory("albert")
    items, total = await user_repo.list_paginated(page=1, size=50, keyword="al")
    assert total == 2
    assert {u.username for u in items} == {"alice", "albert"}


@pytest.mark.integration
async def test_update_last_login(user_repo: UserRepository, user_factory):
    u = await user_factory("login")
    from utils.time import utcnow

    now = utcnow()
    await user_repo.update_last_login(u.id, now)
    fetched = await user_repo.get_by_id(u.id)
    assert fetched is not None
    assert fetched.last_login_at == now
