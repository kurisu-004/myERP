from __future__ import annotations

from datetime import datetime

import pytest
from unittest.mock import AsyncMock

from model.customer import TCustomer
from repository.customer import CustomerRepository
from schema.customer import CustomerOut
from service.customer import CustomerService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_repo() -> CustomerRepository:
    """Create a CustomerRepository with all methods mocked as AsyncMock."""
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.list_all = AsyncMock()
    repo.list_by_ids = AsyncMock()
    return repo


@pytest.fixture
def service(mock_repo: CustomerRepository) -> CustomerService:
    """Create a CustomerService backed by the mock repository."""
    return CustomerService(customers=mock_repo)


def _make_customer(id: int, name: str, parent_id: int | None = None) -> TCustomer:
    """Factory helper to construct a TCustomer instance without a DB session.

    SQLAlchemy 2.x mapped attributes are descriptors that require
    `_sa_instance_state` on the instance, so we go through the real
    constructor (which initialises the instance state) rather than
    `TCustomer.__new__` + attribute assignment.
    """
    return TCustomer(
        id=id,
        name=name,
        parent_id=parent_id,
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )


class TestListCustomers:
    """Tests for CustomerService.list_customers."""

    async def test_normal_with_parent_relationship(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """list_customers resolves parent_name from parent records."""
        # ── arrange ──────────────────────────────────────────────
        child = _make_customer(id=3, name="ChildCorp", parent_id=1)
        parent = _make_customer(id=1, name="ParentCorp")
        all_customers = [child, parent]

        mock_repo.list_all.return_value = all_customers
        mock_repo.list_by_ids.return_value = [parent]

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_awaited_once_with([1])

        assert len(result) == 2

        # Child should have parent_name set
        child_out = next(r for r in result if r.id == 3)
        assert child_out.name == "ChildCorp"
        assert child_out.parent_id == 1
        assert child_out.parent_name == "ParentCorp"

        # Parent should have no parent_name
        parent_out = next(r for r in result if r.id == 1)
        assert parent_out.name == "ParentCorp"
        assert parent_out.parent_id is None
        assert parent_out.parent_name is None

    async def test_empty_list(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """list_customers returns [] when no customers exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.list_all.return_value = []
        mock_repo.list_by_ids.return_value = []

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_not_awaited()

        assert result == []

    async def test_no_parent_id(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """Customers without parent_id have parent_name=None and list_by_ids is never called."""
        # ── arrange ──────────────────────────────────────────────
        c1 = _make_customer(id=10, name="Alpha")
        c2 = _make_customer(id=20, name="Beta")
        all_customers = [c1, c2]

        mock_repo.list_all.return_value = all_customers

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_not_awaited()

        assert len(result) == 2
        for r in result:
            assert r.parent_id is None
            assert r.parent_name is None

    async def test_unmatched_parent_id(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """When a parent_id does not match any existing customer, parent_name should be None."""
        # ── arrange ──────────────────────────────────────────────
        orphan = _make_customer(id=42, name="Orphan", parent_id=999)
        all_customers = [orphan]

        mock_repo.list_all.return_value = all_customers
        mock_repo.list_by_ids.return_value = []  # no parent found

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_awaited_once_with([999])

        assert len(result) == 1
        assert result[0].id == 42
        assert result[0].name == "Orphan"
        assert result[0].parent_id == 999
        assert result[0].parent_name is None
