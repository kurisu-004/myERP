"""RoleRepository 基础 CRUD + 查询."""
import pytest

from model import TRole
from repository.rbac.role import RoleRepository


@pytest.mark.integration
async def test_create_and_get_by_id(role_repo: RoleRepository, role_factory):
    r = await role_factory("ADMIN", name="管理员")
    fetched = await role_repo.get_by_id(r.id)
    assert fetched is not None
    assert fetched.code == "ADMIN"
    assert fetched.name == "管理员"
    assert fetched.is_builtin == 0


@pytest.mark.integration
async def test_get_by_id_returns_none_when_missing(role_repo: RoleRepository):
    assert await role_repo.get_by_id(999_999_999_999) is None


@pytest.mark.integration
async def test_get_by_code(role_repo: RoleRepository, role_factory):
    await role_factory("WORKER")
    r = await role_repo.get_by_code("WORKER")
    assert r is not None
    assert r.code == "WORKER"
    assert await role_repo.get_by_code("NOPE") is None


@pytest.mark.integration
async def test_list_all(role_repo: RoleRepository, role_factory):
    await role_factory("R1")
    await role_factory("R2")
    await role_factory("R3")
    roles = await role_repo.list_all()
    assert {r.code for r in roles} >= {"R1", "R2", "R3"}


@pytest.mark.integration
async def test_list_paginated(role_repo: RoleRepository, role_factory):
    for i in range(25):
        await role_factory(f"R{i:02d}")
    items, total = await role_repo.list_paginated(page=2, size=10)
    assert total >= 25
    assert len(items) == 10


@pytest.mark.integration
async def test_update_role(role_repo: RoleRepository, role_factory):
    r = await role_factory("UPD", name="before")
    r.name = "after"
    r.description = "edit me"
    await role_repo.update(r)
    fetched = await role_repo.get_by_id(r.id)
    assert fetched is not None
    assert fetched.name == "after"
    assert fetched.description == "edit me"


@pytest.mark.integration
async def test_soft_delete(role_repo: RoleRepository, role_factory):
    r = await role_factory("DEL")
    await role_repo.soft_delete(r)
    # 默认查询过滤 deleted_at,拿不到
    assert await role_repo.get_by_id(r.id) is None
    # 包含已删除的查询能拿到
    assert await role_repo.get_by_id(r.id, include_deleted=True) is not None


@pytest.mark.integration
async def test_builtin_cannot_be_deleted(role_repo: RoleRepository, role_factory):
    r = await role_factory("BUILTIN", is_builtin=1)
    with pytest.raises(ValueError):
        await role_repo.soft_delete(r)
