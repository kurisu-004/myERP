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
from service.auth import AuthService

pytestmark = pytest.mark.asyncio


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def mock_users() -> UserRepository:
    repo = UserRepository.__new__(UserRepository)
    repo.get_by_id = AsyncMock()
    repo.get_by_username = AsyncMock()
    repo.touch_login = AsyncMock()
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
