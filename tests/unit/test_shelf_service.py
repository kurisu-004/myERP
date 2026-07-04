"""Unit tests for ShelfService.

Tests cover CRUD operations, error conditions, and the soft-delete guard
that queries active parts via a separate SessionLocal.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.enums import ShelfZone
from model.shelf import TShelf
from repository.shelf import ShelfRepository
from repository.user import UserRoleRepository
from schema.shelf import (
    ShelfCreateRequest,
    ShelfListOut,
    ShelfListQuery,
    ShelfUpdateRequest,
)
from service.shelf import ShelfService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_shelf(
    id: int = 1,
    code: str = "PROD-A1",
    name: str = "生产货架A1",
    zone: str = ShelfZone.PRODUCTION.value,
    location: str | None = "一楼东侧",
    is_active: bool = True,
    deleted_at: datetime | None = None,
) -> TShelf:
    """Build a TShelf instance without a real DB session.

    Uses the constructor directly (SQLAlchemy 2.x needs __init__ to set up
    _sa_instance_state; __new__ alone breaks attribute access).
    """
    return TShelf(
        id=id,
        code=code,
        name=name,
        zone=zone,
        location=location,
        is_active=is_active,
        deleted_at=deleted_at,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def _mock_session_local(result_value: object) -> tuple[AsyncMock, AsyncMock]:
    """Build a SessionLocal replacement that returns *result_value* from the
    first execute().scalar_one_or_none() call.

    Returns ``(mock_cm, mock_session)`` so callers can further configure the
    session mock if needed.

    Notes
    -----
    - ``scalar_one_or_none`` is a **synchronous** call on the result proxy, so
      the result proxy must be a regular ``MagicMock`` (not ``AsyncMock``).
    """
    scalar = MagicMock()
    scalar.scalar_one_or_none.return_value = result_value

    session = AsyncMock()
    session.execute = AsyncMock(return_value=scalar)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock()

    return cm, session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_shelves() -> ShelfRepository:
    """ShelfRepository with all methods mocked as AsyncMock."""
    repo = ShelfRepository.__new__(ShelfRepository)
    repo.list_with_filters = AsyncMock()
    repo.count_with_filters = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_code = AsyncMock()
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    repo.soft_delete = AsyncMock()
    repo.list_by_ids = AsyncMock()
    return repo


@pytest.fixture
def mock_user_roles() -> UserRoleRepository:
    """UserRoleRepository (no methods needed for ShelfService tests)."""
    return UserRoleRepository.__new__(UserRoleRepository)


@pytest.fixture
def service(
    mock_shelves: ShelfRepository,
    mock_user_roles: UserRoleRepository,
) -> ShelfService:
    """ShelfService wired to mock repositories.

    The internal ``_account_count_map`` is replaced with an AsyncMock so tests
    can control its return value without touching ``SessionLocal`` in every
    test.
    """
    svc = ShelfService(shelves=mock_shelves, user_roles=mock_user_roles)
    svc._account_count_map = AsyncMock(return_value={})
    return svc


# ===================================================================
# list_shelves
# ===================================================================

class TestListShelves:
    """ShelfService.list_shelves"""

    async def test_normal_with_zone_filter(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        query = ShelfListQuery(zone=ShelfZone.PRODUCTION)
        s1 = _make_shelf(id=1, code="PROD-A1")
        s2 = _make_shelf(id=2, code="PROD-B1")
        mock_shelves.list_with_filters.return_value = [s1, s2]
        mock_shelves.count_with_filters.return_value = 2
        service._account_count_map = AsyncMock(return_value={1: 3, 2: 5})

        result = await service.list_shelves(query)

        mock_shelves.list_with_filters.assert_awaited_once_with(
            zone="PRODUCTION", is_active=None, limit=200, offset=0,
        )
        mock_shelves.count_with_filters.assert_awaited_once_with(
            zone="PRODUCTION", is_active=None,
        )
        assert isinstance(result, ShelfListOut)
        assert len(result.items) == 2
        assert result.total == 2
        assert result.limit == 200
        assert result.offset == 0
        assert result.items[0].id == 1
        assert result.items[0].account_count == 3
        assert result.items[1].id == 2
        assert result.items[1].account_count == 5

    async def test_empty_list(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        query = ShelfListQuery()
        mock_shelves.list_with_filters.return_value = []
        mock_shelves.count_with_filters.return_value = 0
        service._account_count_map = AsyncMock(return_value={})

        result = await service.list_shelves(query)

        mock_shelves.list_with_filters.assert_awaited_once_with(
            zone=None, is_active=None, limit=200, offset=0,
        )
        mock_shelves.count_with_filters.assert_awaited_once_with(
            zone=None, is_active=None,
        )
        assert result.items == []
        assert result.total == 0


# ===================================================================
# get_shelf
# ===================================================================

class TestGetShelf:
    """ShelfService.get_shelf"""

    async def test_exists(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        s = _make_shelf(id=42)
        mock_shelves.get_by_id.return_value = s
        service._account_count_map = AsyncMock(return_value={42: 3})

        result = await service.get_shelf(42)

        mock_shelves.get_by_id.assert_awaited_once_with(42)
        service._account_count_map.assert_awaited_once_with([42])
        assert result.id == 42
        assert result.account_count == 3

    async def test_not_found(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        mock_shelves.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.get_shelf(999)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND
        mock_shelves.get_by_id.assert_awaited_once_with(999)


# ===================================================================
# create_shelf
# ===================================================================

class TestCreateShelf:
    """ShelfService.create_shelf"""

    @patch("service.shelf.new_id", return_value=1001)
    async def test_normal(
        self,
        mock_new_id: AsyncMock,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        data = ShelfCreateRequest(
            code="NEW-1", name="新货架", zone=ShelfZone.PRODUCTION,
        )
        mock_shelves.get_by_code.return_value = None

        # Simulate DB flush applying server_default timestamps
        async def _create_side_effect(shelf: TShelf) -> None:
            shelf.created_at = datetime(2026, 1, 1)
            shelf.updated_at = datetime(2026, 1, 1)

        mock_shelves.create.side_effect = _create_side_effect

        result = await service.create_shelf(data)

        mock_shelves.get_by_code.assert_awaited_once_with("NEW-1")
        mock_shelves.create.assert_awaited_once()
        created: TShelf = mock_shelves.create.call_args[0][0]
        assert created.id == 1001
        assert created.code == "NEW-1"
        assert created.name == "新货架"
        assert created.zone == "PRODUCTION"
        assert created.location is None
        assert created.is_active is True
        assert result.id == 1001
        assert result.code == "NEW-1"
        assert result.account_count == 0

    @patch("service.shelf.new_id", return_value=1002)
    async def test_duplicate_code(
        self,
        mock_new_id: AsyncMock,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        data = ShelfCreateRequest(
            code="DUP", name="重复货架", zone=ShelfZone.INSPECTION,
        )
        existing = _make_shelf(id=5, code="DUP")
        mock_shelves.get_by_code.return_value = existing

        with pytest.raises(BizError) as exc:
            await service.create_shelf(data)

        assert exc.value.code == ErrCode.BIZ_SHELF_DUPLICATE_CODE
        assert exc.value.http_status == http_status.HTTP_409_CONFLICT
        mock_shelves.get_by_code.assert_awaited_once_with("DUP")
        mock_shelves.create.assert_not_awaited()

    async def test_invalid_zone(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Zone outside {PRODUCTION, INSPECTION} should be rejected."""
        # Use model_construct to bypass Pydantic validation
        data = ShelfCreateRequest.model_construct(
            code="X", name="X", zone="INVALID_ZONE",
        )

        with pytest.raises(BizError) as exc:
            await service.create_shelf(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST
        mock_shelves.get_by_code.assert_not_awaited()
        mock_shelves.create.assert_not_awaited()

    @patch("service.shelf.new_id", return_value=1003)
    async def test_with_location(
        self,
        mock_new_id: AsyncMock,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        data = ShelfCreateRequest(
            code="LOC", name="定位", zone=ShelfZone.INSPECTION, location="二楼品检区",
        )
        mock_shelves.get_by_code.return_value = None

        async def _create_side_effect(shelf: TShelf) -> None:
            shelf.created_at = datetime(2026, 1, 1)
            shelf.updated_at = datetime(2026, 1, 1)

        mock_shelves.create.side_effect = _create_side_effect

        result = await service.create_shelf(data)

        mock_shelves.get_by_code.assert_awaited_once_with("LOC")
        created: TShelf = mock_shelves.create.call_args[0][0]
        assert created.code == "LOC"
        assert created.location == "二楼品检区"
        assert result.location == "二楼品检区"


# ===================================================================
# update_shelf
# ===================================================================

class TestUpdateShelf:
    """ShelfService.update_shelf"""

    async def test_normal(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        s = _make_shelf(id=10, code="PROD-A1", name="旧名", location="旧位置")
        mock_shelves.get_by_id.return_value = s
        data = ShelfUpdateRequest(name="新名字", location="新位置", is_active=False)
        service._account_count_map = AsyncMock(return_value={10: 2})

        result = await service.update_shelf(10, data)

        mock_shelves.get_by_id.assert_awaited_once_with(10)
        assert s.name == "新名字"
        assert s.location == "新位置"
        assert s.is_active is False
        mock_shelves.update.assert_awaited_once_with(s)
        service._account_count_map.assert_awaited_once_with([10])
        assert result.id == 10
        assert result.account_count == 2

    async def test_not_found(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        mock_shelves.get_by_id.return_value = None
        data = ShelfUpdateRequest(name="任意")

        with pytest.raises(BizError) as exc:
            await service.update_shelf(999, data)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND
        mock_shelves.get_by_id.assert_awaited_once_with(999)
        mock_shelves.update.assert_not_awaited()

    async def test_partial_toggle_active(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Only is_active is provided; name and location stay unchanged."""
        s = _make_shelf(
            id=20, code="PROD-B2", name="保留原名", location="原位置", is_active=True,
        )
        mock_shelves.get_by_id.return_value = s
        data = ShelfUpdateRequest(is_active=False)
        service._account_count_map = AsyncMock(return_value={20: 0})

        result = await service.update_shelf(20, data)

        assert s.name == "保留原名"
        assert s.location == "原位置"
        assert s.is_active is False
        mock_shelves.update.assert_awaited_once_with(s)
        assert result.id == 20
        assert result.account_count == 0

    async def test_location_set_to_none(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        """An empty-string location becomes None after strip."""
        s = _make_shelf(id=30, code="PROD-C3", name="货架C", location="某处")
        mock_shelves.get_by_id.return_value = s
        data = ShelfUpdateRequest(location="   ")
        service._account_count_map = AsyncMock(return_value={30: 0})

        result = await service.update_shelf(30, data)

        assert s.location is None
        assert result.location is None


# ===================================================================
# soft_delete_shelf
# ===================================================================

class TestSoftDeleteShelf:
    """ShelfService.soft_delete_shelf"""

    async def test_normal_no_active_parts(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        s = _make_shelf(id=50, code="PROD-DEL")
        mock_shelves.get_by_id.return_value = s
        service._account_count_map = AsyncMock(return_value={50: 0})

        mock_cm, mock_session = _mock_session_local(None)  # no active part

        with patch("core.database.SessionLocal", return_value=mock_cm):
            result = await service.soft_delete_shelf(50)

        mock_shelves.get_by_id.assert_awaited_once_with(50)
        mock_shelves.soft_delete.assert_awaited_once_with(s)
        mock_session.execute.assert_awaited_once()
        service._account_count_map.assert_awaited_once_with([50])
        assert result.id == 50
        assert result.account_count == 0

    async def test_has_active_parts(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        s = _make_shelf(id=60, code="PROD-BUSY")
        mock_shelves.get_by_id.return_value = s

        # Query finds a part → raise BIZ_SHELF_IN_USE
        mock_cm, _mock_session = _mock_session_local(999)

        with patch("core.database.SessionLocal", return_value=mock_cm):
            with pytest.raises(BizError) as exc:
                await service.soft_delete_shelf(60)

        assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE
        assert exc.value.http_status == http_status.HTTP_409_CONFLICT
        mock_shelves.get_by_id.assert_awaited_once_with(60)
        mock_shelves.soft_delete.assert_not_awaited()
        service._account_count_map.assert_not_awaited()

    async def test_not_found(
        self,
        service: ShelfService,
        mock_shelves: ShelfRepository,
    ) -> None:
        mock_shelves.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.soft_delete_shelf(999)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND
        mock_shelves.get_by_id.assert_awaited_once_with(999)
        mock_shelves.soft_delete.assert_not_awaited()
        service._account_count_map.assert_not_awaited()
