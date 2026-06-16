"""AuthService 测试: 登录 / 加载权限 / JWT 解码."""
import time

import jwt
import pytest

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from service.rbac.auth_service import AuthService
from utils.password import hash_password


@pytest.mark.integration
async def test_login_success_returns_token(
    auth_service: AuthService, user_factory
):
    user = await user_factory(
        "alice",
        password_hash=hash_password("secret123"),
    )
    out = await auth_service.login(username="alice", password="secret123")
    assert out.access_token
    assert out.token_type == "bearer"
    # 更新 last_login_at
    reloaded = await auth_service.uow.users.get_by_id(user.id)
    assert reloaded is not None
    assert reloaded.last_login_at is not None


@pytest.mark.integration
async def test_login_wrong_password_raises(
    auth_service: AuthService, user_factory
):
    await user_factory("bob", password_hash=hash_password("right"))
    with pytest.raises(BizError) as exc:
        await auth_service.login(username="bob", password="wrong")
    assert exc.value.code == ErrCode.INVALID_CREDENTIALS
    assert exc.value.http_status == 401


@pytest.mark.integration
async def test_login_unknown_user_raises(auth_service: AuthService):
    with pytest.raises(BizError) as exc:
        await auth_service.login(username="nope", password="x")
    assert exc.value.code == ErrCode.INVALID_CREDENTIALS
    assert exc.value.http_status == 401


@pytest.mark.integration
async def test_login_inactive_user_raises(
    auth_service: AuthService, user_factory
):
    await user_factory("ghost", is_active=0, password_hash=hash_password("p"))
    with pytest.raises(BizError) as exc:
        await auth_service.login(username="ghost", password="p")
    assert exc.value.code == ErrCode.BIZ_USER_DISABLED


@pytest.mark.integration
async def test_token_decode_roundtrip(
    auth_service: AuthService, user_factory
):
    await user_factory("eve", password_hash=hash_password("p"))
    out = await auth_service.login("eve", "p")
    payload = jwt.decode(
        out.access_token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    assert payload["sub"]
    assert int(payload["sub"]) > 0


@pytest.mark.integration
async def test_token_contains_iat_and_exp(
    auth_service: AuthService, user_factory
):
    await user_factory("x", password_hash=hash_password("p"))
    out = await auth_service.login("x", "p")
    payload = jwt.decode(
        out.access_token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] - payload["iat"] == settings.jwt_expire_minutes * 60


@pytest.mark.integration
async def test_load_permissions_aggregates_roles(
    auth_service: AuthService,
    user_factory,
    role_factory,
    permission_factory,
    user_role_factory,
    role_permission_factory,
):
    user = await user_factory("u_perm", password_hash=hash_password("p"))
    r1 = await role_factory("R1")
    r2 = await role_factory("R2")
    p1 = await permission_factory("p1:menu", is_enabled=1)
    p2 = await permission_factory("p2:api", is_enabled=1)
    p_disabled = await permission_factory("p3:off", is_enabled=0)
    await role_permission_factory(r1.id, p1.id)
    await role_permission_factory(r2.id, p2.id)
    await role_permission_factory(r1.id, p_disabled.id)
    await user_role_factory(user.id, r1.id)
    await user_role_factory(user.id, r2.id)

    codes = await auth_service.load_user_permission_codes(user.id)
    assert set(codes) == {"p1:menu", "p2:api"}


@pytest.mark.integration
async def test_load_menu_tree(
    auth_service: AuthService,
    user_factory,
    role_factory,
    permission_factory,
    user_role_factory,
    role_permission_factory,
):
    user = await user_factory("u_menu", password_hash=hash_password("p"))
    role = await role_factory("RMENU")
    parent = await permission_factory("order:menu", type="MENU", sort_order=1)
    child = await permission_factory(
        "order:list",
        type="MENU",
        parent_id=parent.id,
        path="/order/list",
        sort_order=1,
    )
    await role_permission_factory(role.id, parent.id)
    await role_permission_factory(role.id, child.id)
    await user_role_factory(user.id, role.id)
    # API 权限不应该出现在菜单树里
    await permission_factory("order:create", type="API")

    menus = await auth_service.load_user_menus(user.id)
    assert len(menus) == 1
    assert menus[0].code == "order:menu"
    assert len(menus[0].children) == 1
    assert menus[0].children[0].code == "order:list"


@pytest.mark.integration
async def test_load_me_aggregates_user_roles_perms_menus(
    auth_service: AuthService,
    user_factory,
    role_factory,
    permission_factory,
    user_role_factory,
    role_permission_factory,
):
    user = await user_factory("alice", password_hash=hash_password("p"))
    r1 = await role_factory("RME", name="角色ME")
    p1 = await permission_factory("me:menu", type="MENU")
    await role_permission_factory(r1.id, p1.id)
    await user_role_factory(user.id, r1.id)

    me = await auth_service.load_me(user.id)
    assert me.user.id == user.id
    assert me.is_superadmin is False
    assert {role.code for role in me.roles} == {"RME"}
    assert "me:menu" in me.permissions
    assert any(m.code == "me:menu" for m in me.menus)


@pytest.mark.integration
async def test_superadmin_bypasses_and_has_all_codes(
    auth_service: AuthService, user_factory, role_factory, permission_factory
):
    superadmin = await user_factory(
        "superadmin",
        password_hash=hash_password("admin123"),
    )
    # 没有任何角色
    me = await auth_service.load_me(superadmin.id)
    assert me.is_superadmin is True
    assert me.permissions == ["*"]
    assert me.menus == []  # 超管返回空菜单,前端可全部放行
    assert me.roles == []


@pytest.mark.integration
async def test_load_me_user_not_found(auth_service: AuthService):
    with pytest.raises(BizError) as exc:
        await auth_service.load_me(999_999_999_999)
    assert exc.value.code == ErrCode.BIZ_USER_NOT_FOUND
