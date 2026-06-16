"""Auth API 端点集成测试."""
import pytest
from httpx import AsyncClient

from tests.apis.rbac.conftest import _auth


@pytest.mark.integration
async def test_login_success(
    client: AsyncClient, user_factory
):
    from utils.password import hash_password

    await user_factory("alice", password_hash=hash_password("secret123"))
    r = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "secret123"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["expires_in"] > 0


@pytest.mark.integration
async def test_login_wrong_password(
    client: AsyncClient, user_factory
):
    from utils.password import hash_password

    await user_factory("bob", password_hash=hash_password("right"))
    r = await client.post(
        "/api/v1/auth/login", json={"username": "bob", "password": "wrong"}
    )
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == 40101


@pytest.mark.integration
async def test_login_validation_error(
    client: AsyncClient,
):
    r = await client.post("/api/v1/auth/login", json={"username": ""})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == 40001


@pytest.mark.integration
async def test_me_unauthorized(client: AsyncClient):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.integration
async def test_me_with_token(
    client: AsyncClient, uow, user_factory, role_factory, permission_factory
):
    from utils.password import hash_password

    user = await user_factory("eve", password_hash=hash_password("p"))
    role = await role_factory("RM", name="角色M")
    perm = await permission_factory("m:menu", type="MENU")
    await uow.user_roles.assign(user.id, role.id)
    await uow.role_permissions.grant(role.id, perm.id)
    await uow.commit()

    r = await client.post(
        "/api/v1/auth/login", json={"username": "eve", "password": "p"}
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]

    r = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["user"]["username"] == "eve"
    assert data["is_superadmin"] is False
    assert "m:menu" in data["permissions"]
    assert {m["code"] for m in data["menus"]} == {"m:menu"}


@pytest.mark.integration
async def test_superadmin_me_shows_superadmin_flag(
    client: AsyncClient, user_factory
):
    from utils.password import hash_password

    await user_factory("superadmin", password_hash=hash_password("admin"))
    r = await client.post(
        "/api/v1/auth/login", json={"username": "superadmin", "password": "admin"}
    )
    token = r.json()["data"]["access_token"]
    r = await client.get("/api/v1/auth/me", headers=_auth(token))
    body = r.json()["data"]
    assert body["is_superadmin"] is True
    assert body["permissions"] == ["*"]
    assert body["roles"] == []
