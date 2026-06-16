"""PermissionRepository 基础 CRUD + 树查询."""
import pytest

from model import TPermission
from repository.rbac.permission import PermissionRepository


@pytest.mark.integration
async def test_create_and_get_by_id(permission_repo: PermissionRepository, permission_factory):
    p = await permission_factory("order:menu", name="订单管理", type="MENU", path="/order")
    fetched = await permission_repo.get_by_id(p.id)
    assert fetched is not None
    assert fetched.code == "order:menu"
    assert fetched.type == "MENU"


@pytest.mark.integration
async def test_get_by_code(permission_repo: PermissionRepository, permission_factory):
    await permission_factory("part:list")
    p = await permission_repo.get_by_code("part:list")
    assert p is not None
    assert await permission_repo.get_by_code("nope") is None


@pytest.mark.integration
async def test_list_all_filters_disabled(permission_repo: PermissionRepository, permission_factory):
    await permission_factory("a:on", is_enabled=1)
    await permission_factory("b:off", is_enabled=0)
    all_perms = await permission_repo.list_all(include_disabled=True)
    enabled = await permission_repo.list_all(include_disabled=False)
    codes = {p.code for p in all_perms}
    assert "a:on" in codes and "b:off" in codes
    enabled_codes = {p.code for p in enabled}
    assert "a:on" in enabled_codes
    assert "b:off" not in enabled_codes


@pytest.mark.integration
async def test_list_by_type(permission_repo: PermissionRepository, permission_factory):
    await permission_factory("x:menu", type="MENU")
    await permission_factory("x:api", type="API")
    menus = await permission_repo.list_by_type("MENU")
    assert all(p.type == "MENU" for p in menus)
    assert any(p.code == "x:menu" for p in menus)


@pytest.mark.integration
async def test_list_children(permission_repo: PermissionRepository, permission_factory):
    parent = await permission_factory("parent:menu", type="MENU")
    await permission_factory("child:a", type="MENU", parent_id=parent.id, sort_order=1)
    await permission_factory("child:b", type="MENU", parent_id=parent.id, sort_order=2)
    # 无关的不能混入
    await permission_factory("other:menu", type="MENU")
    children = await permission_repo.list_children(parent.id)
    assert {c.code for c in children} == {"child:a", "child:b"}
    assert [c.sort_order for c in children] == [1, 2]


@pytest.mark.integration
async def test_update_permission(permission_repo: PermissionRepository, permission_factory):
    p = await permission_factory("upd:menu")
    p.name = "改名"
    p.is_enabled = 0
    await permission_repo.update(p)
    fetched = await permission_repo.get_by_id(p.id)
    assert fetched is not None
    assert fetched.name == "改名"
    assert fetched.is_enabled == 0


@pytest.mark.integration
async def test_soft_delete(permission_repo: PermissionRepository, permission_factory):
    p = await permission_factory("del:menu")
    await permission_repo.soft_delete(p)
    assert await permission_repo.get_by_id(p.id) is None
    assert await permission_repo.get_by_id(p.id, include_deleted=True) is not None
