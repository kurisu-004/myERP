from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model.enums import ShelfZone, UserRole
from model.shelf import TShelf
from model.user import TUser
from model.user_role import TUserRole
from repository.menu import MenuRepository
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from schema.user import LoginRequest

# 2026-07-15 修复 PytestWarning：pytest-asyncio `asyncio_mode=auto`（pytest.ini）
# 已自动把 `async def test_*` 跑成 async 测试；显式 `pytestmark = pytest.mark.asyncio`
# 是冗余的，且会污染同文件下的 `def test_*`（被加上 asyncio 标记导致 pytest 警告）。
# 删掉全局 pytestmark 即可：async 测试靠 auto 模式自动识别。
from service.auth import AuthService


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def mock_users() -> UserRepository:
    repo = UserRepository.__new__(UserRepository)
    repo.get_by_id = AsyncMock()
    repo.get_by_username = AsyncMock()
    repo.touch_login = AsyncMock()
    repo.increment_refresh_token_version = AsyncMock()
    return repo


@pytest.fixture
def mock_user_roles() -> UserRoleRepository:
    repo = UserRoleRepository.__new__(UserRoleRepository)
    repo.list_by_user = AsyncMock()
    return repo


@pytest.fixture
def mock_shelves() -> ShelfRepository:
    repo = ShelfRepository.__new__(ShelfRepository)
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def mock_menus() -> MenuRepository:
    repo = MenuRepository.__new__(MenuRepository)
    return repo


@pytest.fixture
def service(
    mock_users: UserRepository,
    mock_user_roles: UserRoleRepository,
    mock_shelves: ShelfRepository,
    mock_menus: MenuRepository,
) -> AuthService:
    return AuthService(
        users=mock_users,
        user_roles=mock_user_roles,
        shelves=mock_shelves,
        menus=mock_menus,
    )


# ======================================================================
# Factory helpers
# ======================================================================


def _make_user(
    id: int = 1,
    username: str = "admin",
    password_hash: str = "$2b$12$fakehash",
    full_name: str = "管理员",
    phone: str | None = None,
    is_active: bool = True,
    last_login_at: datetime | None = None,
    deleted_at: datetime | None = None,
    refresh_token_version: int = 0,
) -> TUser:
    """Construct a TUser without a DB session.

    SQLAlchemy 2.x mapped attributes require `_sa_instance_state`, which
    is only set up by the real constructor — so we always go through it.
    Audit fields that are server_default'd get placeholder values.
    """
    return TUser(
        id=id,
        username=username,
        password_hash=password_hash,
        full_name=full_name,
        phone=phone,
        is_active=is_active,
        last_login_at=last_login_at,
        refresh_token_version=refresh_token_version,
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
        deleted_at=deleted_at,
    )


def _make_user_role(
    id: int = 1,
    user_id: int = 1,
    role: str = UserRole.MANAGER.value,
    scope_type: str | None = None,
    scope_id: int | None = None,
) -> TUserRole:
    return TUserRole(
        id=id,
        user_id=user_id,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id,
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
    )


def _make_shelf(
    id: int = 1,
    code: str = "PROD-A1",
    name: str = "生产货架A1",
    zone: str = ShelfZone.PRODUCTION.value,
    location: str | None = "A区",
    is_active: bool = True,
    deleted_at: datetime | None = None,
) -> TShelf:
    return TShelf(
        id=id,
        code=code,
        name=name,
        zone=zone,
        location=location,
        is_active=is_active,
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
        deleted_at=deleted_at,
    )


# ======================================================================
# login
# ======================================================================


class TestLogin:
    """Tests for AuthService.login."""

    async def test_normal_success(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
        mock_menus: MenuRepository,
    ) -> None:
        """login returns token+user with valid username and password and roles."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=42, username="admin", full_name="Administrator")
        mock_users.get_by_username.return_value = u
        role = _make_user_role(id=10, user_id=42, role="MANAGER")
        mock_user_roles.list_by_user.return_value = [role]
        mock_users.touch_login.return_value = u
        data = LoginRequest(username="Admin  ", password="secret")

        # ── act ──────────────────────────────────────────────────
        with (
            patch("service.auth.verify_password", return_value=True),
            patch(
                "service.auth.create_access_token",
                return_value="fake-jwt-token",
            ),
            patch("service.auth.build_menu_tree", AsyncMock(return_value=[])),
        ):
            result = await service.login(data)

        # ── assert ───────────────────────────────────────────────
        # Username is stripped and lowercased before query
        mock_users.get_by_username.assert_awaited_once_with("admin")
        mock_user_roles.list_by_user.assert_awaited_once_with(42)
        mock_users.touch_login.assert_awaited_once_with(u)

        assert result.token == "fake-jwt-token"
        # IdStr is only applied during JSON serialization (when_used="json"),
        # so the Pydantic attribute is still int in Python land.
        assert result.user.id == 42
        assert result.user.username == "admin"
        assert result.user.full_name == "Administrator"
        assert result.user.is_active is True
        assert result.user.roles == ["MANAGER"]
        assert result.user.shelf_ids == []
        assert result.user.menus == []

    async def test_username_not_found(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """login raises 401 when username does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_users.get_by_username.return_value = None
        data = LoginRequest(username="nobody", password="secret")

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("service.auth.verify_password") as mock_verify,
            patch("service.auth.create_access_token") as mock_token,
        ):
            with pytest.raises(BizError) as exc_info:
                await service.login(data)

        mock_users.get_by_username.assert_awaited_once_with("nobody")
        mock_user_roles.list_by_user.assert_not_called()
        mock_verify.assert_not_called()
        mock_token.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.http_status == 401

    async def test_user_deleted(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """login raises 401 when the user is soft-deleted."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=1, username="deleted", deleted_at=datetime(2025, 6, 1, 12, 0, 0))
        mock_users.get_by_username.return_value = u
        data = LoginRequest(username="deleted", password="secret")

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("service.auth.verify_password") as mock_verify,
            patch("service.auth.create_access_token") as mock_token,
        ):
            with pytest.raises(BizError) as exc_info:
                await service.login(data)

        mock_users.get_by_username.assert_awaited_once_with("deleted")
        mock_verify.assert_not_called()
        mock_token.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.message == "invalid username or password"

    async def test_user_inactive(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """login raises 401 when the user is inactive."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=1, username="inactive", is_active=False)
        mock_users.get_by_username.return_value = u
        data = LoginRequest(username="inactive", password="secret")

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("service.auth.verify_password") as mock_verify,
            patch("service.auth.create_access_token") as mock_token,
        ):
            with pytest.raises(BizError) as exc_info:
                await service.login(data)

        mock_users.get_by_username.assert_awaited_once_with("inactive")
        mock_verify.assert_not_called()
        mock_token.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.message == "invalid username or password"

    async def test_wrong_password(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """login raises 401 when the password does not match."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=1, username="admin")
        mock_users.get_by_username.return_value = u
        data = LoginRequest(username="admin", password="wrongpass")

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("service.auth.verify_password", return_value=False),
            patch("service.auth.create_access_token") as mock_token,
        ):
            with pytest.raises(BizError) as exc_info:
                await service.login(data)

        mock_users.get_by_username.assert_awaited_once_with("admin")
        mock_token.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.message == "invalid username or password"

    async def test_user_has_no_roles(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """login raises 403 when user exists but has no roles."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=1, username="noroles")
        mock_users.get_by_username.return_value = u
        mock_user_roles.list_by_user.return_value = []  # no roles
        data = LoginRequest(username="noroles", password="secret")

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("service.auth.verify_password", return_value=True),
            patch("service.auth.create_access_token") as mock_token,
            patch("service.auth.build_menu_tree") as mock_build,
        ):
            with pytest.raises(BizError) as exc_info:
                await service.login(data)

        mock_users.get_by_username.assert_awaited_once_with("noroles")
        mock_user_roles.list_by_user.assert_awaited_once_with(1)
        mock_token.assert_not_called()
        mock_build.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_USER_NO_ROLE
        assert exc_info.value.http_status == 403
        assert "no role" in exc_info.value.message.lower()

    async def test_shelf_account_with_valid_shelf(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """login populates shelf_ids for SHELF_ACCOUNT with valid active shelf."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=1, username="shelfop")
        mock_users.get_by_username.return_value = u
        shelf_role = _make_user_role(
            id=10,
            user_id=1,
            role=UserRole.SHELF_ACCOUNT.value,
            scope_type="shelf",
            scope_id=100,
        )
        mock_user_roles.list_by_user.return_value = [shelf_role]
        shelf = _make_shelf(
            id=100,
            code="PROD-A1",
            zone=ShelfZone.PRODUCTION.value,
            is_active=True,
        )
        mock_shelves.get_by_id.return_value = shelf
        mock_users.touch_login.return_value = u
        data = LoginRequest(username="shelfop", password="secret")

        # ── act ──────────────────────────────────────────────────
        with (
            patch("service.auth.verify_password", return_value=True),
            patch(
                "service.auth.create_access_token",
                return_value="fake-jwt-token",
            ),
            patch("service.auth.build_menu_tree", AsyncMock(return_value=[])),
        ):
            result = await service.login(data)

        # ── assert ───────────────────────────────────────────────
        mock_shelves.get_by_id.assert_awaited_once_with(100)
        assert result.user.shelf_ids == [100]
        assert result.token == "fake-jwt-token"

    async def test_shelf_account_with_inactive_shelf(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """login excludes inactive/deleted shelf_ids for SHELF_ACCOUNT roles."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=1, username="shelfop")
        mock_users.get_by_username.return_value = u

        shelf_role_active = _make_user_role(
            id=10,
            user_id=1,
            role=UserRole.SHELF_ACCOUNT.value,
            scope_type="shelf",
            scope_id=100,
        )
        shelf_role_inactive = _make_user_role(
            id=11,
            user_id=1,
            role=UserRole.SHELF_ACCOUNT.value,
            scope_type="shelf",
            scope_id=101,
        )

        async def _get_shelf_side_effect(shelf_id: int, **kwargs):
            if shelf_id == 100:
                return _make_shelf(
                    id=100,
                    code="PROD-A1",
                    zone=ShelfZone.PRODUCTION.value,
                    is_active=True,
                )
            if shelf_id == 101:
                return _make_shelf(
                    id=101,
                    code="INACTIVE-B2",
                    zone=ShelfZone.PRODUCTION.value,
                    is_active=False,
                )
            return None

        mock_user_roles.list_by_user.return_value = [
            shelf_role_active,
            shelf_role_inactive,
        ]
        mock_shelves.get_by_id.side_effect = _get_shelf_side_effect
        mock_users.touch_login.return_value = u
        data = LoginRequest(username="shelfop", password="secret")

        # ── act ──────────────────────────────────────────────────
        with (
            patch("service.auth.verify_password", return_value=True),
            patch(
                "service.auth.create_access_token",
                return_value="fake-jwt-token",
            ),
            patch("service.auth.build_menu_tree", AsyncMock(return_value=[])),
        ):
            result = await service.login(data)

        # ── assert ───────────────────────────────────────────────
        # Only the active shelf (100) should be in the list
        assert result.user.shelf_ids == [100]


# ======================================================================
# me
# ======================================================================


class TestMe:
    """Tests for AuthService.me."""

    async def test_normal_success(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_menus: MenuRepository,
    ) -> None:
        """me returns CurrentUserOut when user is active and exists."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(id=42, username="admin", full_name="Administrator")
        mock_users.get_by_id.return_value = u

        # ── act ──────────────────────────────────────────────────
        with patch("service.auth.build_menu_tree", AsyncMock(return_value=[])):
            result = await service.me(user_id=42, roles=["MANAGER"], shelf_ids=[100])

        # ── assert ───────────────────────────────────────────────
        mock_users.get_by_id.assert_awaited_once_with(42)
        assert result.id == 42
        assert result.username == "admin"
        assert result.full_name == "Administrator"
        assert result.is_active is True
        assert result.roles == ["MANAGER"]
        assert result.shelf_ids == [100]
        assert result.menus == []

    async def test_user_no_longer_active(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """me raises 401 when the user has been deactivated or deleted."""
        # ── arrange ──────────────────────────────────────────────
        mock_users.get_by_id.return_value = None  # user does not exist

        # ── act / assert ─────────────────────────────────────────
        with patch("service.auth.build_menu_tree") as mock_build:
            with pytest.raises(BizError) as exc_info:
                await service.me(user_id=999, roles=["MANAGER"], shelf_ids=[])

        mock_users.get_by_id.assert_awaited_once_with(999)
        mock_build.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.http_status == 401
        assert "no longer active" in exc_info.value.message.lower()

    async def test_user_deleted(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """me raises 401 when the user is soft-deleted."""
        # ── arrange ──────────────────────────────────────────────
        u = _make_user(
            id=1,
            username="gone",
            is_active=True,
            deleted_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        mock_users.get_by_id.return_value = u  # type: ignore[assignment]
        # get_by_id with default include_deleted=False returns None when deleted_at is set
        # But our mock bypasses that — simulate it by returning None for deleted users
        mock_users.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with patch("service.auth.build_menu_tree") as mock_build:
            with pytest.raises(BizError) as exc_info:
                await service.me(user_id=1, roles=["MANAGER"], shelf_ids=[])

        mock_users.get_by_id.assert_awaited_once_with(1)
        mock_build.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.http_status == 401


# =============================================================================
# 2026-07-10 共享 HMI：_has_wildcard_shelf_account 判定 + login 写入 JWT
# =============================================================================
class TestHasWildcardShelfAccount:
    """`AuthService._has_wildcard_shelf_account` 判定逻辑。

    行为契约：
    - 任一 SHELF_ACCOUNT 行 scope_id IS NULL → True（wildcard）
    - 所有 SHELF_ACCOUNT 行都 scope_id 实际值 → False（锁单架）
    - 没有 SHELF_ACCOUNT 行 → False
    """

    def test_no_shelf_account_role(self) -> None:
        from service.auth import AuthService

        roles: list[TUserRole] = []
        assert AuthService._has_wildcard_shelf_account(roles) is False

    def test_scoped_shelf_account_only(self) -> None:
        from service.auth import AuthService

        roles = [
            TUserRole(
                id=1, user_id=1, role=UserRole.SHELF_ACCOUNT.value,
                scope_type="shelf", scope_id=100,
                created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
            )
        ]
        assert AuthService._has_wildcard_shelf_account(roles) is False

    def test_wildcard_shelf_account(self) -> None:
        from service.auth import AuthService

        roles = [
            TUserRole(
                id=1, user_id=1, role=UserRole.SHELF_ACCOUNT.value,
                scope_type="shelf", scope_id=None,
                created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
            )
        ]
        assert AuthService._has_wildcard_shelf_account(roles) is True

    def test_mixed_wildcard_and_scoped(self) -> None:
        from service.auth import AuthService

        roles = [
            TUserRole(
                id=1, user_id=1, role=UserRole.SHELF_ACCOUNT.value,
                scope_type="shelf", scope_id=100,
                created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
            ),
            TUserRole(
                id=2, user_id=1, role=UserRole.SHELF_ACCOUNT.value,
                scope_type="shelf", scope_id=None,
                created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
            ),
        ]
        # 任一 NULL scope 就足够触发 wildcard
        assert AuthService._has_wildcard_shelf_account(roles) is True

    def test_non_shelf_account_role_with_null_scope(self) -> None:
        """非 SHELF_ACCOUNT 的 scope_id=NULL 不算 wildcard（其他 role 本来就无 scope）。"""
        from service.auth import AuthService

        roles = [
            TUserRole(
                id=1, user_id=1, role=UserRole.MANAGER.value,
                scope_type=None, scope_id=None,
                created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
            )
        ]
        assert AuthService._has_wildcard_shelf_account(roles) is False


class TestCanOperateShelfWildcard:
    """`CurrentUser.can_operate_shelf` 接受 `shelf_wildcard=True` 放行任意架。"""

    def test_wildcard_user_any_shelf(self) -> None:
        from core.permission import CurrentUser

        u = CurrentUser(
            id=1, username="hmi", full_name="车间 HMI", is_active=True,
            roles=(UserRole.SHELF_ACCOUNT.value,),
            shelf_ids=(),  # 空 = 没有具体 scope
            shelf_wildcard=True,
        )
        # 任意 shelf_id 都通过
        assert u.can_operate_shelf(100) is True
        assert u.can_operate_shelf(200) is True
        assert u.can_operate_shelf(0) is True

    def test_scoped_user_only_listed_shelves(self) -> None:
        from core.permission import CurrentUser

        u = CurrentUser(
            id=1, username="op", full_name="工人", is_active=True,
            roles=(UserRole.SHELF_ACCOUNT.value,),
            shelf_ids=(100,),
            shelf_wildcard=False,
        )
        assert u.can_operate_shelf(100) is True
        assert u.can_operate_shelf(200) is False  # 不在列表里

    def test_wildcard_default_false_preserves_legacy(self) -> None:
        """不传 shelf_wildcard = False（兼容历史 JWT 解码路径）。"""
        from core.permission import CurrentUser

        u = CurrentUser(
            id=1, username="op", full_name="工人", is_active=True,
            roles=(UserRole.SHELF_ACCOUNT.value,),
            shelf_ids=(100,),
        )
        # 默认 False → 仍是锁单架行为
        assert u.shelf_wildcard is False
        assert u.can_operate_shelf(100) is True
        assert u.can_operate_shelf(200) is False

    def test_manager_always_operates_any_shelf(self) -> None:
        """MANAGER 越权兜底（与 wildcard 无关）。"""
        from core.permission import CurrentUser

        u = CurrentUser(
            id=1, username="mgr", full_name="管理员", is_active=True,
            roles=(UserRole.MANAGER.value,),
            shelf_ids=(),
            shelf_wildcard=False,
        )
        assert u.can_operate_shelf(999) is True


# =============================================================================
# 2026-07-10 refresh token：AuthService.refresh()
# =============================================================================
class TestRefresh:
    """`AuthService.refresh(refresh_token_str)` 的全部场景：

    - 合法 refresh token → 返回新 LoginResponse + t_user.refresh_token_version +1
    - 过期 / 篡改 refresh → BIZ_AUTH_REFRESH_INVALID 401
    - 拿 access token 来 refresh → BIZ_AUTH_REFRESH_INVALID 401
    - user 已被停用 / 软删 → BIZ_AUTH_REFRESH_INVALID 401
    - 同一 refresh token 用第二次（轮转生效） → BIZ_AUTH_REFRESH_INVALID 401
    - refresh 时重新解析 roles（user 被降级 → 新 token roles 反映最新 DB）
    """

    async def test_happy_path_rotates_version(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
        mock_menus: MenuRepository,
    ) -> None:
        """合法 refresh → 新 token 对 + DB version +1。"""
        from core.security import create_refresh_token

        # 准备 user：DB version=5，refresh token 内 ver=5
        u = _make_user(id=42, username="admin", refresh_token_version=5)
        mock_users.get_by_id.return_value = u

        # refresh 后 version 应变成 6；increment_refresh_token_version 直接 +1 后 flush
        async def _bump_version(user_obj):
            user_obj.refresh_token_version = int(user_obj.refresh_token_version) + 1
            return user_obj
        mock_users.increment_refresh_token_version.side_effect = _bump_version

        role = _make_user_role(id=10, user_id=42, role="MANAGER")
        mock_user_roles.list_by_user.return_value = [role]

        # 构造合法 refresh token（用真 JWT 工厂 + 当前 settings）
        rt = create_refresh_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
            refresh_token_version=5,
        )

        with (
            patch("service.auth.build_menu_tree", AsyncMock(return_value=[])),
            patch(
                "service.auth.create_access_token",
                return_value="new-access-token",
            ),
            patch(
                "service.auth.create_refresh_token",
                return_value="new-refresh-token",
            ),
        ):
            result = await service.refresh(rt)

        # 返回新对
        assert result.token == "new-access-token"
        assert result.refresh_token == "new-refresh-token"
        assert result.user.id == 42
        assert result.user.username == "admin"
        assert result.user.roles == ["MANAGER"]

        # 轮转被调用过一次，且 user 的 version 现在是 6
        mock_users.increment_refresh_token_version.assert_awaited_once_with(u)
        assert u.refresh_token_version == 6

    async def test_invalid_refresh_token_signature(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """坏签名的 refresh token → BIZ_AUTH_REFRESH_INVALID 401。"""
        with pytest.raises(BizError) as exc_info:
            await service.refresh("not.a.valid.jwt")

        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID
        assert exc_info.value.http_status == 401
        # decode 失败 → 不查 DB
        mock_users.get_by_id.assert_not_called()

    async def test_access_token_passed_to_refresh(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """把 access token 当 refresh token 用 → type 不匹配 → 401。"""
        from core.security import create_access_token

        at = create_access_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
        )
        with pytest.raises(BizError) as exc_info:
            await service.refresh(at)

        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID
        mock_users.get_by_id.assert_not_called()

    async def test_user_inactive_after_login(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """用户在 access 期间被停用 → refresh 拒绝。"""
        from core.security import create_refresh_token

        u = _make_user(id=42, username="admin", is_active=False, refresh_token_version=0)
        mock_users.get_by_id.return_value = u

        rt = create_refresh_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
            refresh_token_version=0,
        )

        with pytest.raises(BizError) as exc_info:
            await service.refresh(rt)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID
        # 不发 token、不轮转
        mock_users.increment_refresh_token_version.assert_not_called()

    async def test_user_deleted(
        self,
        service: AuthService,
        mock_users: UserRepository,
    ) -> None:
        """用户被软删 → get_by_id 返回 None → refresh 拒绝。"""
        from core.security import create_refresh_token

        mock_users.get_by_id.return_value = None  # 软删后 get_by_id 返回 None

        rt = create_refresh_token(
            user_id=999,
            username="deleted",
            roles=["MANAGER"],
            shelf_ids=[],
            refresh_token_version=0,
        )

        with pytest.raises(BizError) as exc_info:
            await service.refresh(rt)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID

    async def test_version_mismatch_after_rotation(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """同一 refresh token 用第二次（version 已被轮转）→ 第二次拒绝。"""
        from core.security import create_refresh_token

        # 模拟"第一次 refresh 之后"的状态：DB version 已经是 6
        u = _make_user(id=42, username="admin", refresh_token_version=6)
        mock_users.get_by_id.return_value = u

        # 但 refresh token 是第一次签发的，ver=5（落后）
        rt = create_refresh_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
            refresh_token_version=5,  # ← 落后
        )

        with pytest.raises(BizError) as exc_info:
            await service.refresh(rt)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID
        assert "version mismatch" in exc_info.value.message.lower()
        # version 不对就根本不进入轮转
        mock_users.increment_refresh_token_version.assert_not_called()

    async def test_re_resolves_roles(
        self,
        service: AuthService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """refresh 时重新从 DB 读 roles —— 用户在 access 期间被降级时生效。"""
        from core.security import create_refresh_token

        u = _make_user(id=42, username="admin", refresh_token_version=2)
        mock_users.get_by_id.return_value = u

        # DB 当前 roles 已经被改成只剩 CLERK（之前是 MANAGER）
        current_roles = [
            _make_user_role(id=10, user_id=42, role=UserRole.CLERK.value),
        ]
        mock_user_roles.list_by_user.return_value = current_roles

        async def _bump(user_obj):
            user_obj.refresh_token_version += 1
            return user_obj
        mock_users.increment_refresh_token_version.side_effect = _bump

        rt = create_refresh_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],  # ← 老 roles（access token 里的）
            shelf_ids=[],
            refresh_token_version=2,
        )

        with (
            patch("service.auth.build_menu_tree", AsyncMock(return_value=[])),
            patch("service.auth.create_access_token", return_value="new-at") as ct_at,
            patch("service.auth.create_refresh_token", return_value="new-rt"),
        ):
            result = await service.refresh(rt)

        # 新 token 应反映当前 DB roles（CLERK），不是老 access 里的 MANAGER
        assert result.user.roles == ["CLERK"]
        # 实际签发用的是当前 DB 的 roles
        ct_at.assert_called_once()
        # roles 参数应包含 CLERK，不包含 MANAGER
        kwargs = ct_at.call_args.kwargs
        assert kwargs["roles"] == ["CLERK"]
