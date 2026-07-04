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
from repository.shelf import ShelfRepository
from repository.user import UserRepository, UserRoleRepository
from schema.user import (
    UserCreateRequest,
    UserListQuery,
    UserUpdateRequest,
)
from service.user import UserService

pytestmark = pytest.mark.asyncio


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_users() -> UserRepository:
    repo = UserRepository.__new__(UserRepository)
    repo.list_with_filters = AsyncMock()
    repo.count_with_filters = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_username = AsyncMock()

    # Simulate DB flush: set created_at/updated_at after create/update,
    # since the real columns use server_default but we have no DB session.
    async def _create(user: TUser) -> TUser:
        now = _utcnow()
        if user.created_at is None:
            user.created_at = now
        if user.updated_at is None:
            user.updated_at = now
        return user

    async def _update(user: TUser) -> TUser:
        user.updated_at = _utcnow()
        return user

    repo.create = AsyncMock(side_effect=_create)
    repo.update = AsyncMock(side_effect=_update)
    repo.soft_delete = AsyncMock()
    repo.touch_login = AsyncMock()
    return repo


@pytest.fixture
def mock_user_roles() -> UserRoleRepository:
    repo = UserRoleRepository.__new__(UserRoleRepository)
    repo.list_by_user = AsyncMock(return_value=[])
    repo.get_by_id = AsyncMock()
    repo.create = AsyncMock()
    repo.soft_delete = AsyncMock()
    return repo


@pytest.fixture
def mock_shelves() -> ShelfRepository:
    repo = ShelfRepository.__new__(ShelfRepository)
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def service(
    mock_users: UserRepository,
    mock_user_roles: UserRoleRepository,
    mock_shelves: ShelfRepository,
) -> UserService:
    return UserService(users=mock_users, user_roles=mock_user_roles, shelves=mock_shelves)


# ============================================================
# Factory helpers (construct ORM instances without a DB session)
# ============================================================


def _utcnow() -> datetime:
    return datetime.utcnow()


def _make_user(id: int, **kwargs: object) -> TUser:
    """Build a TUser instance via __init__ (no DB needed)."""
    return TUser(
        id=id,
        username=str(kwargs.get("username", f"user{id}")),
        password_hash=str(kwargs.get("password_hash", "hashed_default")),
        full_name=str(kwargs.get("full_name", f"User {id}")),
        phone=kwargs.get("phone"),  # may be None
        is_active=bool(kwargs.get("is_active", True)),
        last_login_at=kwargs.get("last_login_at"),
        deleted_at=kwargs.get("deleted_at"),
        created_at=kwargs.get("created_at", _utcnow()),
        updated_at=kwargs.get("updated_at", _utcnow()),
    )


def _make_role(id: int, user_id: int, role: str, **kwargs: object) -> TUserRole:
    """Build a TUserRole instance via __init__ (no DB needed)."""
    return TUserRole(
        id=id,
        user_id=user_id,
        role=role,
        scope_type=kwargs.get("scope_type"),
        scope_id=kwargs.get("scope_id"),
        deleted_at=kwargs.get("deleted_at"),
        created_at=kwargs.get("created_at", _utcnow()),
        created_by=kwargs.get("created_by"),
        updated_at=kwargs.get("updated_at", _utcnow()),
        updated_by=kwargs.get("updated_by"),
    )


def _make_shelf(id: int, **kwargs: object) -> TShelf:
    """Build a TShelf instance via __init__ (no DB needed)."""
    return TShelf(
        id=id,
        code=str(kwargs.get("code", f"SHELF-{id}")),
        name=str(kwargs.get("name", f"Shelf {id}")),
        zone=str(kwargs.get("zone", "PRODUCTION")),
        location=kwargs.get("location"),
        is_active=bool(kwargs.get("is_active", True)),
        deleted_at=kwargs.get("deleted_at"),
        created_at=kwargs.get("created_at", _utcnow()),
        created_by=kwargs.get("created_by"),
        updated_at=kwargs.get("updated_at", _utcnow()),
        updated_by=kwargs.get("updated_by"),
    )


# ============================================================
# list_users
# ============================================================


class TestListUsers:
    async def test_normal_with_roles_populated(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """list_users returns paginated results with roles populated via _to_out."""
        user1 = _make_user(1001, username="alice", full_name="Alice")
        user2 = _make_user(1002, username="bob", full_name="Bob")
        mock_users.list_with_filters.return_value = [user1, user2]
        mock_users.count_with_filters.return_value = 2

        role = _make_role(5001, user_id=1001, role="MANAGER")
        mock_user_roles.list_by_user.side_effect = [
            [role],  # user1 -> one role
            [],      # user2 -> no roles
        ]

        result = await service.list_users(UserListQuery())

        mock_users.list_with_filters.assert_awaited_once_with(
            username_like=None, is_active=None, limit=50, offset=0
        )
        mock_users.count_with_filters.assert_awaited_once_with(
            username_like=None, is_active=None
        )
        assert result.total == 2
        assert result.limit == 50
        assert result.offset == 0
        assert len(result.items) == 2

        alice_out = result.items[0]
        assert alice_out.username == "alice"
        assert len(alice_out.roles) == 1
        assert alice_out.roles[0].role == "MANAGER"

        bob_out = result.items[1]
        assert bob_out.username == "bob"
        assert len(bob_out.roles) == 0

    async def test_list_users_empty(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """list_users returns empty list when no users match."""
        mock_users.list_with_filters.return_value = []
        mock_users.count_with_filters.return_value = 0

        result = await service.list_users(UserListQuery(limit=10, offset=0))

        assert result.total == 0
        assert result.items == []

    async def test_list_users_with_filters(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """list_users passes username_like and is_active to filter methods."""
        user = _make_user(1001, username="admin")
        mock_users.list_with_filters.return_value = [user]
        mock_users.count_with_filters.return_value = 1

        query = UserListQuery(username_like="adm", is_active=True, limit=20, offset=5)
        result = await service.list_users(query)

        mock_users.list_with_filters.assert_awaited_once_with(
            username_like="adm", is_active=True, limit=20, offset=5
        )
        mock_users.count_with_filters.assert_awaited_once_with(
            username_like="adm", is_active=True
        )
        assert len(result.items) == 1
        assert result.items[0].username == "admin"


# ============================================================
# get_user
# ============================================================


class TestGetUser:
    async def test_get_user_exists(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """get_user returns UserOut when user exists."""
        user = _make_user(1001, username="charlie", full_name="Charlie")
        mock_users.get_by_id.return_value = user

        result = await service.get_user(1001)

        mock_users.get_by_id.assert_awaited_once_with(1001)
        assert result.id == 1001
        assert result.username == "charlie"
        assert result.roles == []

    async def test_get_user_not_found_404(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """get_user raises BIZ_USER_ACCOUNT_NOT_FOUND (404) when user does not exist."""
        mock_users.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.get_user(999)

        assert exc.value.code == ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND
        assert exc.value.http_status == 404


# ============================================================
# create_user
# ============================================================


class TestCreateUser:
    async def test_create_user_normal(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """create_user creates a new active user with lowercased username and hashed password."""
        mock_users.get_by_username.return_value = None

        with (
            patch("service.user.new_id", return_value=1001),
            patch("service.user.hash_password", return_value="hashed_secret"),
        ):
            data = UserCreateRequest(
                username=" NewUser ",
                password="secret123",
                full_name="  New User  ",
                phone="13900000000",
            )
            result = await service.create_user(data)

        mock_users.get_by_username.assert_awaited_once_with("newuser")
        mock_users.create.assert_awaited_once()

        created_user: TUser = mock_users.create.call_args[0][0]
        assert created_user.id == 1001
        assert created_user.username == "newuser"
        assert created_user.password_hash == "hashed_secret"
        assert created_user.full_name == "New User"
        assert created_user.phone == "13900000000"
        assert created_user.is_active is True

        assert result.username == "newuser"
        assert result.full_name == "New User"
        assert result.is_active is True
        assert result.roles == []

    async def test_create_user_phone_none(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """create_user stores None when phone is None."""
        mock_users.get_by_username.return_value = None

        with (
            patch("service.user.new_id", return_value=1002),
            patch("service.user.hash_password", return_value="hashed_pw"),
        ):
            data = UserCreateRequest(
                username="phoneless",
                password="pw",
                full_name="No Phone",
                phone=None,
            )
            await service.create_user(data)

        created_user: TUser = mock_users.create.call_args[0][0]
        assert created_user.phone is None

    async def test_create_user_empty_phone_stored_as_none(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """create_user stores None when phone is whitespace-only string."""
        mock_users.get_by_username.return_value = None
        with (
            patch("service.user.new_id", return_value=1003),
            patch("service.user.hash_password", return_value="h"),
        ):
            data = UserCreateRequest(
                username="emptyphone",
                password="pw",
                full_name="Empty Phone",
                phone="   ",
            )
            await service.create_user(data)

        created_user: TUser = mock_users.create.call_args[0][0]
        assert created_user.phone is None

    async def test_create_user_duplicate_username_409(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """create_user raises BIZ_USER_DUPLICATE_USERNAME (409) when username exists."""
        existing = _make_user(999, username="newuser")
        mock_users.get_by_username.return_value = existing

        data = UserCreateRequest(username="NEWUSER", password="pw", full_name="Dup")

        with pytest.raises(BizError) as exc:
            await service.create_user(data)

        assert exc.value.code == ErrCode.BIZ_USER_DUPLICATE_USERNAME
        assert exc.value.http_status == 409
        mock_users.create.assert_not_awaited()


# ============================================================
# update_user
# ============================================================


class TestUpdateUser:
    async def test_update_user_normal(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """update_user updates full_name and phone."""
        user = _make_user(1001, full_name="Old Name", phone="111")
        mock_users.get_by_id.return_value = user

        data = UserUpdateRequest(
            full_name=" New Name  ",
            phone=" 222 ",
            password=None,
            is_active=None,
        )
        result = await service.update_user(1001, data)

        mock_users.get_by_id.assert_awaited_once_with(1001)
        mock_users.update.assert_awaited_once_with(user)
        assert user.full_name == "New Name"
        assert user.phone == "222"
        assert user.password_hash == "hashed_default"  # unchanged
        assert user.is_active is True  # unchanged

        assert result.full_name == "New Name"
        assert result.phone == "222"

    async def test_update_user_not_found_404(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """update_user raises BIZ_USER_ACCOUNT_NOT_FOUND (404)."""
        mock_users.get_by_id.return_value = None

        data = UserUpdateRequest(full_name="Ghost")
        with pytest.raises(BizError) as exc:
            await service.update_user(999, data)

        assert exc.value.code == ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND
        assert exc.value.http_status == 404
        mock_users.update.assert_not_awaited()

    async def test_update_user_password_change(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """update_user hashes and stores the new password."""
        user = _make_user(1001, password_hash="old_hashed")
        mock_users.get_by_id.return_value = user

        data = UserUpdateRequest(password="new_secret")
        with patch("service.user.hash_password", return_value="hashed_new"):
            result = await service.update_user(1001, data)

        assert user.password_hash == "hashed_new"
        assert result.roles == []

    async def test_update_user_deactivate(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """update_user sets is_active to False."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        data = UserUpdateRequest(is_active=False)
        await service.update_user(1001, data)

        assert user.is_active is False

    async def test_update_user_empty_phone_stored_as_none(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """update_user stores None when phone is whitespace-only."""
        user = _make_user(1001, phone="old")
        mock_users.get_by_id.return_value = user

        data = UserUpdateRequest(phone="   ")
        await service.update_user(1001, data)

        assert user.phone is None


# ============================================================
# soft_delete_user
# ============================================================


class TestSoftDeleteUser:
    async def test_soft_delete_user_normal(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """soft_delete_user soft-deletes the user and returns the user out."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        result = await service.soft_delete_user(1001)

        mock_users.get_by_id.assert_awaited_once_with(1001)
        mock_users.soft_delete.assert_awaited_once_with(user)
        assert result.id == 1001

    async def test_soft_delete_user_not_found_404(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """soft_delete_user raises BIZ_USER_ACCOUNT_NOT_FOUND (404)."""
        mock_users.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.soft_delete_user(999)

        assert exc.value.code == ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND
        assert exc.value.http_status == 404
        mock_users.soft_delete.assert_not_awaited()


# ============================================================
# list_user_roles
# ============================================================


class TestListUserRoles:
    async def test_list_user_roles_normal_with_shelf_info(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """list_user_roles returns role list with shelf info for scoped roles."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        shelf = _make_shelf(5001, code="PROD-A1", name="Prod Shelf A1")
        role = _make_role(2001, user_id=1001, role="SHELF_ACCOUNT",
                          scope_type="shelf", scope_id=5001)
        mock_user_roles.list_by_user.return_value = [role]
        mock_shelves.list_by_ids.return_value = [shelf]

        result = await service.list_user_roles(1001)

        mock_users.get_by_id.assert_awaited_once_with(1001)
        mock_user_roles.list_by_user.assert_awaited_once_with(1001)
        mock_shelves.list_by_ids.assert_awaited_once_with([5001])

        assert len(result) == 1
        assert result[0].role == "SHELF_ACCOUNT"
        assert result[0].scope_type == "shelf"
        assert result[0].scope_id == 5001
        assert result[0].shelf_code == "PROD-A1"
        assert result[0].shelf_name == "Prod Shelf A1"

    async def test_list_user_roles_user_not_found_404(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """list_user_roles raises BIZ_USER_ACCOUNT_NOT_FOUND (404)."""
        mock_users.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.list_user_roles(999)

        assert exc.value.code == ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_list_user_roles_empty(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """list_user_roles returns empty list when user has no roles."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user
        mock_user_roles.list_by_user.return_value = []

        result = await service.list_user_roles(1001)

        assert result == []


# ============================================================
# add_role
# ============================================================


class TestAddRole:
    async def test_add_role_shelf_account_valid_scope(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """add_role creates SHELF_ACCOUNT role with valid shelf scope."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        shelf = _make_shelf(5001, code="PROD-A1", name="Prod Shelf",
                            zone="PRODUCTION", is_active=True)
        mock_shelves.get_by_id.return_value = shelf
        mock_shelves.list_by_ids.return_value = [shelf]

        with patch("service.user.new_id", return_value=3001):
            result = await service.add_role(
                1001, UserRole.SHELF_ACCOUNT, "shelf", 5001
            )

        mock_users.get_by_id.assert_awaited_once_with(1001)
        mock_shelves.get_by_id.assert_awaited_once_with(5001)
        mock_user_roles.create.assert_awaited_once()
        created_role: TUserRole = mock_user_roles.create.call_args[0][0]
        assert created_role.id == 3001
        assert created_role.user_id == 1001
        assert created_role.role == "SHELF_ACCOUNT"
        assert created_role.scope_type == "shelf"
        assert created_role.scope_id == 5001

        assert result.role == "SHELF_ACCOUNT"
        assert result.scope_type == "shelf"
        assert result.scope_id == 5001
        assert result.shelf_code == "PROD-A1"
        assert result.shelf_name == "Prod Shelf"

    async def test_add_role_manager_rejects_scope(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """add_role raises BIZ_INVALID_VALUE when MANAGER role is given a scope."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        with pytest.raises(BizError) as exc:
            await service.add_role(1001, UserRole.MANAGER, "shelf", 5001)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400
        mock_user_roles.create.assert_not_awaited()

    async def test_add_role_shelf_account_missing_scope_400(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """add_role raises BIZ_INVALID_VALUE when SHELF_ACCOUNT lacks scope."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        with pytest.raises(BizError) as exc:
            await service.add_role(1001, UserRole.SHELF_ACCOUNT, None, None)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400

    async def test_add_role_shelf_account_wrong_scope_type_400(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """add_role raises BIZ_INVALID_VALUE when SHELF_ACCOUNT scope_type is not 'shelf'."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        with pytest.raises(BizError) as exc:
            await service.add_role(1001, UserRole.SHELF_ACCOUNT, "warehouse", 5001)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400

    async def test_add_role_shelf_account_shelf_not_found_404(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """add_role raises BIZ_SHELF_NOT_FOUND (404) when shelf does not exist."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user
        mock_shelves.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.add_role(1001, UserRole.SHELF_ACCOUNT, "shelf", 999)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_add_role_shelf_account_shelf_inactive_400(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """add_role raises BIZ_SHELF_NOT_FOUND when shelf is inactive."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        shelf = _make_shelf(5001, zone="PRODUCTION", is_active=False)
        mock_shelves.get_by_id.return_value = shelf

        with pytest.raises(BizError) as exc:
            await service.add_role(1001, UserRole.SHELF_ACCOUNT, "shelf", 5001)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == 400

    async def test_add_role_user_not_found_404(
        self,
        service: UserService,
        mock_users: UserRepository,
    ) -> None:
        """add_role raises BIZ_USER_ACCOUNT_NOT_FOUND (404)."""
        mock_users.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.add_role(999, UserRole.MANAGER, None, None)

        assert exc.value.code == ErrCode.BIZ_USER_ACCOUNT_NOT_FOUND
        assert exc.value.http_status == 404


# ============================================================
# remove_role
# ============================================================


class TestRemoveRole:
    async def test_remove_role_normal(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """remove_role soft-deletes the role."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        role = _make_role(2001, user_id=1001, role="MANAGER")
        mock_user_roles.get_by_id.return_value = role

        await service.remove_role(1001, 2001)

        mock_users.get_by_id.assert_awaited_once_with(1001)
        mock_user_roles.get_by_id.assert_awaited_once_with(2001)
        mock_user_roles.soft_delete.assert_awaited_once_with(role)

    async def test_remove_role_not_found_for_user_404(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """remove_role raises BIZ_USER_ROLE_NOT_FOUND (404) when role does not exist."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        # Scenario: role does not exist at all
        mock_user_roles.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.remove_role(1001, 999)

        assert exc.value.code == ErrCode.BIZ_USER_ROLE_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_remove_role_wrong_user_404(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """remove_role raises BIZ_USER_ROLE_NOT_FOUND when role belongs to another user."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        # Role belongs to user 9999, not 1001
        role = _make_role(2001, user_id=9999, role="MANAGER")
        mock_user_roles.get_by_id.return_value = role

        with pytest.raises(BizError) as exc:
            await service.remove_role(1001, 2001)

        assert exc.value.code == ErrCode.BIZ_USER_ROLE_NOT_FOUND
        assert exc.value.http_status == 404
        mock_user_roles.soft_delete.assert_not_awaited()

    async def test_remove_role_already_deleted_404(
        self,
        service: UserService,
        mock_users: UserRepository,
        mock_user_roles: UserRoleRepository,
    ) -> None:
        """remove_role raises BIZ_USER_ROLE_NOT_FOUND when role is already soft-deleted."""
        user = _make_user(1001)
        mock_users.get_by_id.return_value = user

        role = _make_role(2001, user_id=1001, role="MANAGER",
                          deleted_at=_utcnow())
        mock_user_roles.get_by_id.return_value = role

        with pytest.raises(BizError) as exc:
            await service.remove_role(1001, 2001)

        assert exc.value.code == ErrCode.BIZ_USER_ROLE_NOT_FOUND
        assert exc.value.http_status == 404
        mock_user_roles.soft_delete.assert_not_awaited()
