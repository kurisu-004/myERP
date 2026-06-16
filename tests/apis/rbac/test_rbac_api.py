"""RBAC 管理 API 集成测试: 用户/角色/权限 CRUD + 超管白名单."""
import pytest
from httpx import AsyncClient

from tests.apis.rbac.conftest import _auth


# -------- superadmin 登录 helper --------
async def _login_as(client: AsyncClient, username: str, password: str) -> str:
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


@pytest.mark.integration
async def test_user_crud_flow(client: AsyncClient, user_factory):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    token = await _login_as(client, "superadmin", "admin")

    # list
    r = await client.get("/api/v1/rbac/users", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert "items" in r.json()["data"]

    # create
    r = await client.post(
        "/api/v1/rbac/users",
        headers=_auth(token),
        json={
            "employee_no": "U20260001",
            "username": "newbie",
            "name": "新人",
            "password": "pwd123456",
            "role_ids": [],
        },
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["data"]["id"]

    # detail
    r = await client.get(
        f"/api/v1/rbac/users/{new_id}", headers=_auth(token)
    )
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "newbie"

    # update
    r = await client.put(
        f"/api/v1/rbac/users/{new_id}",
        headers=_auth(token),
        json={"name": "改改名", "is_active": 0},
    )
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "改改名"
    assert r.json()["data"]["is_active"] == 0

    # delete
    r = await client.delete(
        f"/api/v1/rbac/users/{new_id}", headers=_auth(token)
    )
    assert r.status_code == 200


@pytest.mark.integration
async def test_non_superadmin_cannot_manage_users(
    client: AsyncClient, user_factory
):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    await user_factory("worker", password_hash=hash_password("p"))
    token = await _login_as(client, "worker", "p")

    r = await client.get("/api/v1/rbac/users", headers=_auth(token))
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == 40300


@pytest.mark.integration
async def test_role_crud_flow(
    client: AsyncClient, user_factory, permission_factory
):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    perm = await permission_factory("p1:menu", type="MENU")
    token = await _login_as(client, "superadmin", "admin")

    r = await client.post(
        "/api/v1/rbac/roles",
        headers=_auth(token),
        json={
            "code": "R_NEW",
            "name": "新角色",
            "description": "测试",
            "permission_ids": [perm.id],
        },
    )
    assert r.status_code == 201, r.text
    role_id = r.json()["data"]["id"]

    r = await client.get("/api/v1/rbac/roles", headers=_auth(token))
    assert r.status_code == 200
    assert any(item["code"] == "R_NEW" for item in r.json()["data"]["items"])

    r = await client.put(
        f"/api/v1/rbac/roles/{role_id}",
        headers=_auth(token),
        json={"name": "改名", "permission_ids": []},
    )
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "改名"

    r = await client.delete(
        f"/api/v1/rbac/roles/{role_id}", headers=_auth(token)
    )
    assert r.status_code == 200


@pytest.mark.integration
async def test_assign_user_roles(
    client: AsyncClient, user_factory, role_factory
):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    r1 = await role_factory("R1")
    r2 = await role_factory("R2")
    user = await user_factory("target")
    token = await _login_as(client, "superadmin", "admin")

    r = await client.put(
        f"/api/v1/rbac/users/{user.id}/roles",
        headers=_auth(token),
        json={"role_ids": [r1.id, r2.id]},
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"/api/v1/rbac/users/{user.id}", headers=_auth(token)
    )
    detail = r.json()["data"]
    assert {rl["code"] for rl in detail["roles"]} == {"R1", "R2"}


@pytest.mark.integration
async def test_permission_crud(
    client: AsyncClient, user_factory
):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    token = await _login_as(client, "superadmin", "admin")

    r = await client.post(
        "/api/v1/rbac/permissions",
        headers=_auth(token),
        json={
            "code": "test:menu",
            "name": "测试菜单",
            "type": "MENU",
            "path": "/test",
            "icon": "Box",
            "component": "@/views/test/Index.vue",
            "sort_order": 1,
            "is_enabled": 1,
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]

    r = await client.get("/api/v1/rbac/permissions", headers=_auth(token))
    assert r.status_code == 200
    assert any(p["code"] == "test:menu" for p in r.json()["data"])

    r = await client.put(
        f"/api/v1/rbac/permissions/{pid}",
        headers=_auth(token),
        json={"name": "改名", "is_enabled": 0},
    )
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "改名"
    assert r.json()["data"]["is_enabled"] == 0


@pytest.mark.integration
async def test_rbac_endpoints_require_token(client: AsyncClient):
    r = await client.get("/api/v1/rbac/users")
    assert r.status_code == 401
    r = await client.get("/api/v1/rbac/roles")
    assert r.status_code == 401
    r = await client.get("/api/v1/rbac/permissions")
    assert r.status_code == 401


@pytest.mark.integration
async def test_assign_roles_clears_old(
    client: AsyncClient, user_factory, role_factory, uow
):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    r1 = await role_factory("ROLD")
    r2 = await role_factory("RNEW")
    user = await user_factory("u")
    await uow.user_roles.assign(user.id, r1.id)
    await uow.commit()
    token = await _login_as(client, "superadmin", "admin")

    r = await client.put(
        f"/api/v1/rbac/users/{user.id}/roles",
        headers=_auth(token),
        json={"role_ids": [r2.id]},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/v1/rbac/users/{user.id}", headers=_auth(token))
    assert {rl["code"] for rl in r.json()["data"]["roles"]} == {"RNEW"}
