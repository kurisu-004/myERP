"""RBACService 测试: 用户管理/角色管理/权限管理/分配."""
import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TUser
from service.rbac.rbac_service import RBACService
from utils.id_gen import new_id
from utils.password import hash_password, verify_password


@pytest.mark.integration
async def test_create_user_with_roles(
    rbac_service: RBACService, role_factory
):
    r1 = await role_factory("R_NEW1")
    r2 = await role_factory("R_NEW2")
    out = await rbac_service.create_user(
        employee_no="U20260001",
        username="newbie",
        name="新人",
        password="pwd123456",
        role_ids=[r1.id, r2.id],
    )
    assert out.username == "newbie"
    assert out.employee_no == "U20260001"
    # 角色已分配
    links = await rbac_service.uow.user_roles.list_by_user(out.id)
    assert {l.code for l in links} == {"R_NEW1", "R_NEW2"}


@pytest.mark.integration
async def test_create_user_duplicate_username_raises(
    rbac_service: RBACService, user_factory
):
    await user_factory("dup")
    with pytest.raises(BizError) as exc:
        await rbac_service.create_user(
            employee_no="U2",
            username="dup",
            name="x",
            password="pwd123456",
            role_ids=[],
        )
    assert exc.value.code == ErrCode.BIZ_USER_DUPLICATE


@pytest.mark.integration
async def test_update_user_replaces_roles(
    rbac_service: RBACService, user_factory, role_factory
):
    r1 = await role_factory("RUP1")
    r2 = await role_factory("RUP2")
    r3 = await role_factory("RUP3")
    user = await user_factory("u_upd")
    await rbac_service.uow.user_roles.assign(user.id, r1.id)
    await rbac_service.uow.user_roles.assign(user.id, r2.id)

    out = await rbac_service.update_user(
        user_id=user.id,
        name="新名",
        role_ids=[r3.id],
    )
    assert out.name == "新名"
    links = await rbac_service.uow.user_roles.list_by_user(user.id)
    assert {l.code for l in links} == {"RUP3"}


@pytest.mark.integration
async def test_update_user_password(
    rbac_service: RBACService, user_factory
):
    user = await user_factory("u_pwd", password_hash=hash_password("old"))
    await rbac_service.update_user(user_id=user.id, password="newpass1")
    reloaded = await rbac_service.uow.users.get_by_id(user.id)
    assert reloaded is not None
    assert verify_password("newpass1", reloaded.password_hash)
    assert not verify_password("old", reloaded.password_hash)


@pytest.mark.integration
async def test_delete_user_soft(
    rbac_service: RBACService, user_factory
):
    user = await user_factory("u_del")
    await rbac_service.delete_user(user.id)
    assert await rbac_service.uow.users.get_by_id(user.id) is None


@pytest.mark.integration
async def test_get_user_detail_with_roles(
    rbac_service: RBACService, user_factory, role_factory
):
    r = await role_factory("R_GET", name="Get角色")
    user = await user_factory("u_get")
    await rbac_service.uow.user_roles.assign(user.id, r.id)
    detail = await rbac_service.get_user_detail(user.id)
    assert detail.username == "u_get"
    assert {rl.code for rl in detail.roles} == {"R_GET"}


@pytest.mark.integration
async def test_create_role_with_permissions(
    rbac_service: RBACService, permission_factory
):
    p1 = await permission_factory("p1:r")
    p2 = await permission_factory("p2:r")
    out = await rbac_service.create_role(
        code="R_CREATE",
        name="创建角色",
        description="desc",
        permission_ids=[p1.id, p2.id],
    )
    assert out.code == "R_CREATE"
    codes = await rbac_service.uow.role_permissions.list_codes_by_role(out.id)
    assert set(codes) == {"p1:r", "p2:r"}


@pytest.mark.integration
async def test_create_role_duplicate_code_raises(
    rbac_service: RBACService, role_factory
):
    await role_factory("DUP_ROLE")
    with pytest.raises(BizError) as exc:
        await rbac_service.create_role(
            code="DUP_ROLE", name="x", description=None, permission_ids=[]
        )
    assert exc.value.code == ErrCode.CONFLICT


@pytest.mark.integration
async def test_update_role_replaces_permissions(
    rbac_service: RBACService, role_factory, permission_factory
):
    r = await role_factory("RUPD")
    p1 = await permission_factory("pu1")
    p2 = await permission_factory("pu2")
    p3 = await permission_factory("pu3")
    await rbac_service.uow.role_permissions.grant(r.id, p1.id)
    await rbac_service.uow.role_permissions.grant(r.id, p2.id)

    out = await rbac_service.update_role(
        role_id=r.id,
        name="改名",
        description="new desc",
        permission_ids=[p3.id],
    )
    assert out.name == "改名"
    codes = await rbac_service.uow.role_permissions.list_codes_by_role(r.id)
    assert codes == ["pu3"]


@pytest.mark.integration
async def test_delete_builtin_role_blocked(
    rbac_service: RBACService, role_factory
):
    r = await role_factory("BUILTIN", is_builtin=1)
    with pytest.raises(BizError) as exc:
        await rbac_service.delete_role(r.id)
    assert exc.value.code == ErrCode.BIZ_ROLE_BUILTIN


@pytest.mark.integration
async def test_create_permission(rbac_service: RBACService):
    out = await rbac_service.create_permission(
        code="new:menu",
        name="新菜单",
        type="MENU",
        parent_id=None,
        path="/new",
        icon="Box",
        component="@/views/new/Index.vue",
        sort_order=10,
        is_enabled=1,
    )
    assert out.code == "new:menu"
    fetched = await rbac_service.uow.permissions.get_by_code("new:menu")
    assert fetched is not None
