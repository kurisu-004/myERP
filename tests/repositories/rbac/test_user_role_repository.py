"""UserRoleRepository: 用户-角色 关联."""
import pytest

from repository.rbac.user_role import UserRoleRepository


@pytest.mark.integration
async def test_assign_and_list(
    user_role_repo: UserRoleRepository,
    user_factory,
    role_factory,
):
    u = await user_factory("u1")
    r1 = await role_factory("R1")
    r2 = await role_factory("R2")
    await user_role_repo.assign(u.id, r1.id)
    await user_role_repo.assign(u.id, r2.id)
    roles = await user_role_repo.list_by_user(u.id)
    assert {r.code for r in roles} == {"R1", "R2"}


@pytest.mark.integration
async def test_assign_idempotent(
    user_role_repo: UserRoleRepository,
    user_factory,
    role_factory,
):
    u = await user_factory("u_idem")
    r = await role_factory("RIDEM")
    await user_role_repo.assign(u.id, r.id)
    await user_role_repo.assign(u.id, r.id)
    roles = await user_role_repo.list_by_user(u.id)
    assert len(roles) == 1


@pytest.mark.integration
async def test_revoke(
    user_role_repo: UserRoleRepository,
    user_factory,
    role_factory,
):
    u = await user_factory("u_rev")
    r1 = await role_factory("RREV1")
    r2 = await role_factory("RREV2")
    await user_role_repo.assign(u.id, r1.id)
    await user_role_repo.assign(u.id, r2.id)
    await user_role_repo.revoke(u.id, r1.id)
    roles = await user_role_repo.list_by_user(u.id)
    assert {r.code for r in roles} == {"RREV2"}


@pytest.mark.integration
async def test_replace_user_roles(
    user_role_repo: UserRoleRepository,
    user_factory,
    role_factory,
):
    u = await user_factory("u_rep")
    r1 = await role_factory("REP1")
    r2 = await role_factory("REP2")
    r3 = await role_factory("REP3")
    await user_role_repo.assign(u.id, r1.id)
    await user_role_repo.assign(u.id, r2.id)
    await user_role_repo.replace(u.id, [r3.id])
    roles = await user_role_repo.list_by_user(u.id)
    assert {r.code for r in roles} == {"REP3"}


@pytest.mark.integration
async def test_list_users_by_role(
    user_role_repo: UserRoleRepository,
    user_factory,
    role_factory,
):
    u1 = await user_factory("uu1")
    u2 = await user_factory("uu2")
    r = await role_factory("R_USERS")
    await user_role_repo.assign(u1.id, r.id)
    await user_role_repo.assign(u2.id, r.id)
    users = await user_role_repo.list_users_by_role(r.id)
    assert {u.username for u in users} == {"uu1", "uu2"}
