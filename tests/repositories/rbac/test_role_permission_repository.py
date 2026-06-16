"""RolePermissionRepository: 角色-权限 关联."""
import pytest

from repository.rbac.role_permission import RolePermissionRepository


@pytest.mark.integration
async def test_grant_and_list(
    role_permission_repo: RolePermissionRepository,
    role_factory,
    permission_factory,
):
    r = await role_factory("R1")
    p1 = await permission_factory("p1:menu", type="MENU")
    p2 = await permission_factory("p2:api", type="API")
    await role_permission_repo.grant(r.id, p1.id)
    await role_permission_repo.grant(r.id, p2.id)
    perms = await role_permission_repo.list_permissions_by_role(r.id)
    assert {p.code for p in perms} == {"p1:menu", "p2:api"}


@pytest.mark.integration
async def test_grant_idempotent(
    role_permission_repo: RolePermissionRepository,
    role_factory,
    permission_factory,
):
    r = await role_factory("RIDEM")
    p = await permission_factory("pidem:menu")
    await role_permission_repo.grant(r.id, p.id)
    await role_permission_repo.grant(r.id, p.id)
    perms = await role_permission_repo.list_permissions_by_role(r.id)
    assert len(perms) == 1


@pytest.mark.integration
async def test_revoke(
    role_permission_repo: RolePermissionRepository,
    role_factory,
    permission_factory,
):
    r = await role_factory("RREV")
    p1 = await permission_factory("prev1:menu")
    p2 = await permission_factory("prev2:menu")
    await role_permission_repo.grant(r.id, p1.id)
    await role_permission_repo.grant(r.id, p2.id)
    await role_permission_repo.revoke(r.id, p1.id)
    perms = await role_permission_repo.list_permissions_by_role(r.id)
    assert {p.code for p in perms} == {"prev2:menu"}


@pytest.mark.integration
async def test_replace_role_permissions(
    role_permission_repo: RolePermissionRepository,
    role_factory,
    permission_factory,
):
    r = await role_factory("RREP")
    p1 = await permission_factory("prep1:menu")
    p2 = await permission_factory("prep2:menu")
    p3 = await permission_factory("prep3:menu")
    await role_permission_repo.grant(r.id, p1.id)
    await role_permission_repo.grant(r.id, p2.id)
    await role_permission_repo.replace(r.id, [p3.id])
    perms = await role_permission_repo.list_permissions_by_role(r.id)
    assert {p.code for p in perms} == {"prep3:menu"}


@pytest.mark.integration
async def test_list_codes_by_role(
    role_permission_repo: RolePermissionRepository,
    role_factory,
    permission_factory,
):
    r = await role_factory("RCODES")
    await permission_factory("disabled:menu", is_enabled=0)
    p1 = await permission_factory("c1:menu", is_enabled=1)
    p2 = await permission_factory("c2:api", is_enabled=1)
    await role_permission_repo.grant(r.id, p1.id)
    await role_permission_repo.grant(r.id, p2.id)
    codes = await role_permission_repo.list_codes_by_role(r.id, enabled_only=True)
    assert set(codes) == {"c1:menu", "c2:api"}
