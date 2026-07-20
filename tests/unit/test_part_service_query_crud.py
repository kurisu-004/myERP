"""Unit tests for PartService query and CRUD methods.

Tests cover:
- list_parts          (tests 1-3)
- get_part            (tests 4-5)
- list_events         (tests 6-7)
- create_part         (tests 8-13)
- create_parts_batch  (tests 14-16)
- update_part         (tests 17-19)
- soft_delete_part    (tests 20-21)
- _to_out             (tests 22-24)
- list_pickable_parts / list_pickable_parts_all_shelves
  (2026-07-10 加：next_process_id IS NULL + shelf_ids 过滤)

Extra edge cases beyond the 24 numbered scenarios: list_events with no worker_ids,
update_part partial field update, and _to_out shelf location.

Mock strategy: ALL repository methods are AsyncMock instances injected into the
service via fixtures. Model objects are constructed via the SQLAlchemy model
constructor (sets up _sa_instance_state for mutation compatibility).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import configure_mappers

from core.error_code import ErrCode
from core.exception import BizError
from model import TCustomer, TPart, TPartEvent, TShelf, TWorker

# Force SQLAlchemy to configure all ORM mappers before we create model instances.
# Without this, InstrumentedAttribute descriptors may have None impl in certain
# import-order scenarios, causing AttributeError on __set__/__get__.
configure_mappers()
from model.enums import PartEventType, PartSortKey, PartStatus, SortDir
from schema.part import (
    PartBatchCreateItemFailure,
    PartBatchCreateRequest,
    PartBatchCreateResult,
    PartCreateRequest,
    PartListOut,
    PartListQuery,
    PartOut,
    PartUpdateRequest,
)
from service.part import PartService

pytestmark = pytest.mark.asyncio


# ======================================================================
# Factory helpers — construct model instances without a DB session
# ======================================================================


def _make_part(id: int, customer_id: int, **kwargs) -> TPart:
    """Construct a TPart using the SQLAlchemy constructor (sets up _sa_instance_state)."""
    return TPart(
        id=id,
        customer_id=customer_id,
        serial_no=kwargs.get("serial_no", "L2507001"),
        name=kwargs.get("name", "Test Part"),
        drawing_no=kwargs.get("drawing_no", "DWG-001"),
        applicant_name=kwargs.get("applicant_name", "Applicant"),
        quantity=kwargs.get("quantity", 1),
        unit_price=kwargs.get("unit_price", Decimal("100")),
        total_price=kwargs.get("total_price", Decimal("100")),
        request_date=kwargs.get("request_date", date(2025, 1, 1)),
        planned_delivery_date=kwargs.get("planned_delivery_date", date(2025, 2, 1)),
        actual_delivery_date=kwargs.get("actual_delivery_date"),
        status=kwargs.get("status", PartStatus.PENDING.value),
        location=kwargs.get("location", "OFFICE"),
        is_urgent=kwargs.get("is_urgent", False),
        current_holder_id=kwargs.get("current_holder_id"),
        placed_at=kwargs.get("placed_at"),
        assembly_id=kwargs.get("assembly_id"),
        created_at=kwargs.get("created_at", datetime(2025, 1, 1, 0, 0, 0)),
        created_by=kwargs.get("created_by"),
        updated_at=kwargs.get("updated_at", datetime(2025, 1, 1, 0, 0, 0)),
        updated_by=kwargs.get("updated_by"),
        deleted_at=kwargs.get("deleted_at"),
    )


def _make_customer(
    id: int,
    name: str,
    parent_id: int | None = None,
    serial_prefix: str | None = "L",
) -> TCustomer:
    """Construct a TCustomer using the SQLAlchemy constructor.

    2026-07-09 起默认 serial_prefix='L'，与旧测试用 `code_for_parent` 兜底
    返回 'L' 的行为对齐；不传则设 None（用于测「未配置前缀」场景）。
    """
    return TCustomer(
        id=id, name=name, parent_id=parent_id, serial_prefix=serial_prefix,
    )


def _make_worker(id: int, name: str = "Worker") -> TWorker:
    """Construct a TWorker using the SQLAlchemy constructor."""
    return TWorker(id=id, name=name, badge_code=f"B{id:03d}", is_active=True)


def _make_shelf(id: int, code: str = "PROD-A1", zone: str = "PRODUCTION") -> TShelf:
    """Construct a TShelf using the SQLAlchemy constructor."""
    return TShelf(id=id, code=code, name=f"Shelf {code}", zone=zone, is_active=True)


def _make_event(part_id: int, **kwargs) -> TPartEvent:
    """Construct a TPartEvent using the SQLAlchemy constructor."""
    return TPartEvent(
        id=kwargs.get("id", 1001),
        part_id=part_id,
        worker_id=kwargs.get("worker_id"),
        event_type=kwargs.get("event_type", PartEventType.CREATED.value),
        from_status=kwargs.get("from_status"),
        to_status=kwargs.get("to_status", PartStatus.PENDING.value),
        drawing_code=kwargs.get("drawing_code"),
        badge_code=kwargs.get("badge_code"),
        note=kwargs.get("note"),
        created_at=kwargs.get("created_at", datetime(2025, 1, 1, 10, 0, 0)),
    )


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def mock_parts() -> AsyncMock:
    mock = AsyncMock()
    mock.list_with_filters = AsyncMock()
    mock.count_with_filters = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.get_by_serial = AsyncMock()
    mock.create = AsyncMock()
    mock.update = AsyncMock()
    mock.soft_delete = AsyncMock()
    mock.list_children = AsyncMock()
    return mock


@pytest.fixture
def mock_customers() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.list_by_ids = AsyncMock()
    mock.list_children = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_workers() -> AsyncMock:
    mock = AsyncMock()
    mock.list_by_ids = AsyncMock()
    mock.list_with_filters = AsyncMock()
    mock.get_by_badge_code = AsyncMock()
    return mock


@pytest.fixture
def mock_events() -> AsyncMock:
    mock = AsyncMock()
    mock.list_by_part = AsyncMock()
    mock.create = AsyncMock()
    return mock


@pytest.fixture
def mock_serial_counters() -> AsyncMock:
    mock = AsyncMock()
    mock.acquire_serial = AsyncMock()
    return mock


@pytest.fixture
def mock_shelves() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.list_by_ids = AsyncMock()
    return mock


@pytest.fixture
def mock_work_type_process() -> AsyncMock:
    mock = AsyncMock()
    mock.list_process_ids_by_work_type = AsyncMock()
    return mock


@pytest.fixture
def service(
    mock_parts: AsyncMock,
    mock_customers: AsyncMock,
    mock_workers: AsyncMock,
    mock_events: AsyncMock,
    mock_serial_counters: AsyncMock,
    mock_shelves: AsyncMock,
) -> PartService:
    return PartService(
        parts=mock_parts,
        customers=mock_customers,
        workers=mock_workers,
        events=mock_events,
        serial_counters=mock_serial_counters,
        shelves=mock_shelves,
    )


@pytest.fixture
def service_with_wtp(
    mock_parts: AsyncMock,
    mock_customers: AsyncMock,
    mock_workers: AsyncMock,
    mock_events: AsyncMock,
    mock_serial_counters: AsyncMock,
    mock_shelves: AsyncMock,
    mock_work_type_process: AsyncMock,
) -> PartService:
    """PartService 装了 work_type_process 仓储（PICK_UP 列表需要）。"""
    return PartService(
        parts=mock_parts,
        customers=mock_customers,
        workers=mock_workers,
        events=mock_events,
        serial_counters=mock_serial_counters,
        shelves=mock_shelves,
        work_type_process=mock_work_type_process,
    )


# ======================================================================
# list_parts
# ======================================================================


class TestListParts:
    """Tests for PartService.list_parts."""

    async def test_customer_id_provided_leaf(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """Leaf customer_id provided → resolves parent_id != null → no cascade list_children call."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="ChildCorp", parent_id=99)
        mock_customers.get_by_id.return_value = cust

        part = _make_part(id=1, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 5

        # _to_list_out dependencies
        mock_customers.list_by_ids.return_value = [cust]

        query = PartListQuery(customer_id='10')

        # ── act ──────────────────────────────────────────────────
        result = await service.list_parts(query)

        # ── assert ───────────────────────────────────────────────
        mock_customers.get_by_id.assert_awaited_once_with(10)
        mock_customers.list_children.assert_not_awaited()
        mock_parts.list_with_filters.assert_awaited_once_with(
            customer_ids_in=[10],
            statuses=None,
            is_urgent=None,
            keyword=None,
            sort_by=PartSortKey.PLANNED_DELIVERY_DATE,
            sort_dir=SortDir.ASC,
            limit=50,
            offset=0,
        )
        mock_parts.count_with_filters.assert_awaited_once_with(
            customer_ids_in=[10],
            statuses=None,
            is_urgent=None,
            keyword=None,
        )
        assert isinstance(result, PartListOut)
        assert len(result.items) == 1
        assert result.total == 5
        assert result.limit == 50
        assert result.offset == 0

    async def test_customer_id_root_cascades_to_children(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """Root customer (parent_id is None) → expands to [self + children]."""
        # ── arrange ──────────────────────────────────────────────
        root = _make_customer(id=10, name="RootCorp", parent_id=None)
        mock_customers.get_by_id.return_value = root
        children = [
            _make_customer(id=20, name="Kid1", parent_id=10),
            _make_customer(id=21, name="Kid2", parent_id=10),
        ]
        mock_customers.list_children.return_value = children
        mock_customers.list_by_ids.return_value = [root, *children]

        part = _make_part(id=1, customer_id=20)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        query = PartListQuery(customer_id='10')

        # ── act ──────────────────────────────────────────────────
        await service.list_parts(query)

        # ── assert ───────────────────────────────────────────────
        mock_customers.get_by_id.assert_awaited_once_with(10)
        mock_customers.list_children.assert_awaited_once_with(10)
        mock_parts.list_with_filters.assert_awaited_once_with(
            customer_ids_in=[10, 20, 21],
            statuses=None,
            is_urgent=None,
            keyword=None,
            sort_by=PartSortKey.PLANNED_DELIVERY_DATE,
            sort_dir=SortDir.ASC,
            limit=50,
            offset=0,
        )

    async def test_customer_id_none(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """customer_id is None → skips customer lookup, customer_ids_in stays None."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=2, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        query = PartListQuery(customer_id=None)

        # ── act ──────────────────────────────────────────────────
        result = await service.list_parts(query)

        # ── assert ───────────────────────────────────────────────
        mock_customers.get_by_id.assert_not_awaited()
        mock_customers.list_children.assert_not_awaited()
        mock_parts.list_with_filters.assert_awaited_once_with(
            customer_ids_in=None,
            statuses=None,
            is_urgent=None,
            keyword=None,
            sort_by=PartSortKey.PLANNED_DELIVERY_DATE,
            sort_dir=SortDir.ASC,
            limit=50,
            offset=0,
        )
        mock_parts.count_with_filters.assert_awaited_once()
        assert isinstance(result, PartListOut)
        assert len(result.items) == 1

    async def test_customer_not_found(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """customer provided but not found → BizError(BIZ_CUSTOMER_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        mock_customers.get_by_id.return_value = None
        query = PartListQuery(customer_id='999')

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.list_parts(query)

        mock_customers.get_by_id.assert_awaited_once_with(999)
        mock_parts.list_with_filters.assert_not_awaited()
        mock_parts.count_with_filters.assert_not_awaited()
        assert exc_info.value.code == ErrCode.BIZ_CUSTOMER_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_sort_by_serial_no(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """sort_by=SERIAL_NO is forwarded to repository."""
        # ── arrange ──────────────────────────────────────────────
        mock_customers.list_by_ids.return_value = []
        mock_parts.list_with_filters.return_value = []
        mock_parts.count_with_filters.return_value = 0

        query = PartListQuery(
            sort_by=PartSortKey.SERIAL_NO, sort_dir=SortDir.ASC
        )

        # ── act ──────────────────────────────────────────────────
        await service.list_parts(query)

        # ── assert ───────────────────────────────────────────────
        call_kwargs = mock_parts.list_with_filters.await_args.kwargs
        assert call_kwargs["sort_by"] == PartSortKey.SERIAL_NO
        assert call_kwargs["sort_dir"] == SortDir.ASC

    async def test_sort_by_drawing_no_desc(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """sort_by=DRAWING_NO with DESC ordering works."""
        mock_customers.list_by_ids.return_value = []
        mock_parts.list_with_filters.return_value = []
        mock_parts.count_with_filters.return_value = 0

        query = PartListQuery(
            sort_by=PartSortKey.DRAWING_NO, sort_dir=SortDir.DESC
        )

        await service.list_parts(query)

        call_kwargs = mock_parts.list_with_filters.await_args.kwargs
        assert call_kwargs["sort_by"] == PartSortKey.DRAWING_NO
        assert call_kwargs["sort_dir"] == SortDir.DESC

    async def test_sort_by_name_asc(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """sort_by=NAME is forwarded."""
        mock_customers.list_by_ids.return_value = []
        mock_parts.list_with_filters.return_value = []
        mock_parts.count_with_filters.return_value = 0

        query = PartListQuery(sort_by=PartSortKey.NAME)

        await service.list_parts(query)

        call_kwargs = mock_parts.list_with_filters.await_args.kwargs
        assert call_kwargs["sort_by"] == PartSortKey.NAME

    async def test_keyword_forwarded_non_none(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """非空 keyword 透传给 repository（drawing_no 改子串匹配后，repository 仍按 kw 过滤）。

        SQL 层 'ilike %kw%' vs 'ilike kw%' 的语义差异由 repository 集成测试保证；
        本测只锁定 plumbing：service 必须把 keyword 原样下传。
        """
        # drawing_no='ABC-123-XYZ', name='完全不相关名字'
        part = _make_part(
            id=1,
            customer_id=10,
            drawing_no="ABC-123-XYZ",
            name="完全不相关名字",
        )
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1
        mock_customers.list_by_ids.return_value = []

        query = PartListQuery(keyword="123")

        result = await service.list_parts(query)

        call_kwargs = mock_parts.list_with_filters.await_args.kwargs
        assert call_kwargs["keyword"] == "123"
        # call_kwargs 的列表关键字（count）也带 keyword
        count_kwargs = mock_parts.count_with_filters.await_args.kwargs
        assert count_kwargs["keyword"] == "123"
        assert len(result.items) == 1
        assert result.total == 1


# ======================================================================
# get_part
# ======================================================================


class TestGetPart:
    """Tests for PartService.get_part."""

    async def test_part_exists(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """Part exists → calls get_by_id, _to_out, returns PartOut."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=42, customer_id=10)
        mock_parts.get_by_id.return_value = part

        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        # ── act ──────────────────────────────────────────────────
        result = await service.get_part(42)

        # ── assert ───────────────────────────────────────────────
        mock_parts.get_by_id.assert_awaited_once_with(42)
        assert isinstance(result, PartOut)
        assert result.id == 42
        assert result.serial_no == "L2507001"
        assert result.name == "Test Part"

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """Part not found → BizError(BIZ_PART_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.get_part(999)

        mock_parts.get_by_id.assert_awaited_once_with(999)
        assert exc_info.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert "part 999" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# list_events
# ======================================================================


class TestListEvents:
    """Tests for PartService.list_events."""

    async def test_part_exists_with_workers(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_events: AsyncMock,
        mock_workers: AsyncMock,
    ) -> None:
        """Part exists, has events with workers → returns list with worker names filled."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10)
        mock_parts.get_by_id.return_value = part

        event1 = _make_event(part_id=1, worker_id=501, event_type=PartEventType.PICKED_UP.value)
        event2 = _make_event(part_id=1, worker_id=None, event_type=PartEventType.CREATED.value, id=1002)
        mock_events.list_by_part.return_value = [event1, event2]

        worker = _make_worker(id=501, name="WorkerWang")
        mock_workers.list_with_filters.return_value = [worker]

        # ── act ──────────────────────────────────────────────────
        result = await service.list_events(1)

        # ── assert ───────────────────────────────────────────────
        mock_parts.get_by_id.assert_awaited_once_with(1)
        mock_events.list_by_part.assert_awaited_once_with(1)
        mock_workers.list_with_filters.assert_awaited_once_with(
            is_active=None, limit=max(100, 1)
        )

        assert len(result) == 2

        # Event with worker_id → worker_name filled
        assert result[0].worker_id == 501
        assert result[0].worker_name == "WorkerWang"
        assert result[0].event_type == PartEventType.PICKED_UP.value

        # Event without worker_id → worker_name is None
        assert result[1].worker_id is None
        assert result[1].worker_name is None

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """Part not found → BizError(BIZ_PART_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.list_events(999)

        mock_parts.get_by_id.assert_awaited_once_with(999)
        assert exc_info.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_no_worker_ids_skips_worker_lookup(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_events: AsyncMock,
        mock_workers: AsyncMock,
    ) -> None:
        """Part exists, events have no worker_ids → worker lookup is skipped."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10)
        mock_parts.get_by_id.return_value = part

        event = _make_event(part_id=1, worker_id=None)
        mock_events.list_by_part.return_value = [event]

        # ── act ──────────────────────────────────────────────────
        result = await service.list_events(1)

        # ── assert ───────────────────────────────────────────────
        mock_workers.list_with_filters.assert_not_awaited()
        assert len(result) == 1
        assert result[0].worker_name is None


# ======================================================================
# create_part
# ======================================================================


class TestCreatePart:
    """Tests for PartService.create_part."""

    async def test_first_level_customer(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_events: AsyncMock,
        mock_serial_counters: AsyncMock,
    ) -> None:
        """Normal — first-level customer (parent_id=None) → validates, acquires serial, creates part+event."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="FirstLevel")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]  # for _to_out
        mock_serial_counters.acquire_serial.return_value = "L2507001"

        data = PartCreateRequest(
            name="Test Part",
            drawing_no="DWG-001",
            applicant_name="Applicant",
            quantity=2,
            unit_price=Decimal("150"),
            total_price=Decimal("300"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1),
            is_urgent=False, customer_id='10',
        )

        # ── act ──────────────────────────────────────────────────
        with patch("service.part.new_id", return_value=9001):
            result = await service.create_part(data)

        # ── assert repository calls ──────────────────────────────
        mock_customers.get_by_id.assert_awaited_once_with(10)
        mock_serial_counters.acquire_serial.assert_awaited_once_with("L")
        mock_parts.create.assert_awaited_once()
        mock_events.create.assert_awaited_once()

        # ── assert created part fields ───────────────────────────
        created_part: TPart = mock_parts.create.call_args[0][0]
        assert created_part.id == 9001
        assert created_part.serial_no == "L2507001"
        assert created_part.name == "Test Part"
        assert created_part.drawing_no == "DWG-001"
        assert created_part.applicant_name == "Applicant"
        assert created_part.quantity == 2
        assert created_part.unit_price == Decimal("150")
        assert created_part.total_price == Decimal("300")
        assert created_part.request_date == date(2025, 1, 1)
        assert created_part.planned_delivery_date == date(2025, 2, 1)
        assert created_part.actual_delivery_date is None
        assert created_part.status == PartStatus.PENDING.value
        assert created_part.location == "OFFICE"
        assert created_part.is_urgent is False
        assert created_part.customer_id == 10
        assert created_part.assembly_id is None

        # ── assert event created ─────────────────────────────────
        created_event: TPartEvent = mock_events.create.call_args[0][0]
        assert created_event.part_id == 9001
        assert created_event.event_type == PartEventType.CREATED.value
        assert created_event.to_status == PartStatus.PENDING.value
        assert created_event.from_status is None

        # ── assert return value ──────────────────────────────────
        assert isinstance(result, PartOut)
        assert result.id == 9001
        assert result.serial_no == "L2507001"

    async def test_first_level_customer_with_delivery_note_fields(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_events: AsyncMock,
        mock_serial_counters: AsyncMock,
    ) -> None:
        """PR-F (2026-07-17): create_part 透传 order_no / system_delivery_date / note."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="FirstLevel")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]
        mock_serial_counters.acquire_serial.return_value = "F2507001"

        data = PartCreateRequest(
            name="Test Part",
            drawing_no="DWG-001",
            applicant_name="Applicant",
            quantity=2,
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1),
            is_urgent=False,
            customer_id='10',
            order_no="ORD-2025-001",
            system_delivery_date=date(2025, 2, 5),
            note="加急单",
        )

        # ── act ──────────────────────────────────────────────────
        with patch("service.part.new_id", return_value=9001):
            result = await service.create_part(data)

        # ── assert created part fields ───────────────────────────
        created_part: TPart = mock_parts.create.call_args[0][0]
        assert created_part.order_no == "ORD-2025-001"
        assert created_part.system_delivery_date == date(2025, 2, 5)
        assert created_part.note == "加急单"

        # ── assert response ──────────────────────────────────────
        assert result.order_no == "ORD-2025-001"
        assert result.system_delivery_date == date(2025, 2, 5)
        assert result.note == "加急单"

    async def test_second_level_customer(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_events: AsyncMock,
        mock_serial_counters: AsyncMock,
    ) -> None:
        """Normal — second-level customer (parent_id set) → fetches parent for serial code."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=20, name="ChildCorp", parent_id=1)
        parent = _make_customer(id=1, name="ParentCorp")

        # First get_by_id call returns cust, second returns parent
        mock_customers.get_by_id.side_effect = [cust, parent]
        mock_customers.list_by_ids.return_value = [cust, parent]  # for _to_out
        mock_serial_counters.acquire_serial.return_value = "L2507001"

        data = PartCreateRequest(
            name="Child Part",
            drawing_no="DWG-002",
            quantity=1,
            unit_price=Decimal("50"),
            total_price=Decimal("50"),
            request_date=date(2025, 3, 1),
            planned_delivery_date=date(2025, 4, 1), customer_id='20',
        )

        # ── act ──────────────────────────────────────────────────
        with patch("service.part.new_id", return_value=9001):
            result = await service.create_part(data)

        # ── assert ───────────────────────────────────────────────
        # Two calls: one for cust, one for parent
        assert mock_customers.get_by_id.call_count == 2
        mock_customers.get_by_id.assert_any_await(20)
        mock_customers.get_by_id.assert_any_await(1)
        mock_serial_counters.acquire_serial.assert_awaited_once_with("L")
        mock_parts.create.assert_awaited_once()
        mock_events.create.assert_awaited_once()

        assert isinstance(result, PartOut)

    async def test_customer_not_found(
        self,
        service: PartService,
        mock_customers: AsyncMock,
    ) -> None:
        """Customer not found → BizError(BIZ_CUSTOMER_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        mock_customers.get_by_id.return_value = None

        data = PartCreateRequest(
            name="Test",
            drawing_no="DWG",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='999',
        )

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.create_part(data)

        mock_customers.get_by_id.assert_awaited_once_with(999)
        assert exc_info.value.code == ErrCode.BIZ_CUSTOMER_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_parent_customer_not_found(
        self,
        service: PartService,
        mock_customers: AsyncMock,
    ) -> None:
        """Parent customer not found → BizError(BIZ_CUSTOMER_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=20, name="ChildCorp", parent_id=1)
        mock_customers.get_by_id.side_effect = [cust, None]  # parent not found

        data = PartCreateRequest(
            name="Test",
            drawing_no="DWG",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='20',
        )

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.create_part(data)

        assert mock_customers.get_by_id.call_count == 2
        assert exc_info.value.code == ErrCode.BIZ_CUSTOMER_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_no_serial_code_configured(
        self,
        service: PartService,
        mock_customers: AsyncMock,
    ) -> None:
        """No serial code configured for customer → BizError(BIZ_CUSTOMER_NOT_FOUND, 400)."""
        # ── arrange ──────────────────────────────────────────────
        # 一级客户名 "UnknownCorp" 不在 PARENT_TO_CODE 兜底，且 serial_prefix 为 None
        # → resolve_root_prefix 双兜底都拿不到，触发 BIZ_CUSTOMER_NOT_FOUND 400。
        cust = _make_customer(id=10, name="UnknownCorp", serial_prefix=None)
        mock_customers.get_by_id.return_value = cust

        data = PartCreateRequest(
            name="Test",
            drawing_no="DWG",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='10',
        )

        # ── act ──────────────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.create_part(data)

        mock_customers.get_by_id.assert_awaited_once_with(10)
        assert exc_info.value.code == ErrCode.BIZ_CUSTOMER_NOT_FOUND
        assert exc_info.value.http_status == 400
        assert "未配置一级客户「UnknownCorp」" in exc_info.value.message

    async def test_total_price_auto_calculated(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_events: AsyncMock,
        mock_serial_counters: AsyncMock,
    ) -> None:
        """total_price = unit_price * quantity when total_price is None."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="FirstLevel")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]
        mock_serial_counters.acquire_serial.return_value = "L2507001"

        data = PartCreateRequest(
            name="Test",
            drawing_no="DWG",
            quantity=5,
            unit_price=Decimal("200"),
            total_price=None,  # auto-calculate
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='10',
        )

        # ── act ──────────────────────────────────────────────────
        with patch("service.part.new_id", return_value=9001):
            result = await service.create_part(data)

        # ── assert total_price was auto-calculated ───────────────
        created_part: TPart = mock_parts.create.call_args[0][0]
        assert created_part.total_price == Decimal("1000")  # 5 * 200


# ======================================================================
# create_parts_batch
# ======================================================================


class TestCreatePartsBatch:
    """Tests for PartService.create_parts_batch."""

    async def test_all_items_succeed(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_events: AsyncMock,
        mock_serial_counters: AsyncMock,
    ) -> None:
        """All items pass validation → creates all, returns created list with empty failed."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="FirstLevel")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        mock_serial_counters.acquire_serial.return_value = "L2507001"

        item0 = PartCreateRequest(
            name="Part A",
            drawing_no="DWGA",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='10',
        )
        item1 = PartCreateRequest(
            name="Part B",
            drawing_no="DWGB",
            quantity=2,
            unit_price=Decimal("20"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='10',
        )
        payload = PartBatchCreateRequest(items=[item0, item1])

        # ── act ──────────────────────────────────────────────────
        with patch("service.part.new_id", return_value=9001):
            result = await service.create_parts_batch(payload)

        # ── assert ───────────────────────────────────────────────
        assert isinstance(result, PartBatchCreateResult)
        assert len(result.created) == 2
        assert result.failed == []

    async def test_some_items_fail(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
    ) -> None:
        """Some items fail validation → returns failed list; good items are NOT created."""
        # ── arrange ──────────────────────────────────────────────
        # Item 0 uses customer_id='999' (雪花 ID 字符串；customer 999 已软删前不存在)；
        # item 1 uses customer_id='10'（一级客户）
        cust = _make_customer(id=10, name="FirstLevel")
        mock_customers.get_by_id.side_effect = [None, cust]

        item0 = PartCreateRequest(
            name="Bad Part",
            drawing_no="DWGX",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='999',
        )
        item1 = PartCreateRequest(
            name="Good Part",
            drawing_no="DWGY",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='10',
        )
        payload = PartBatchCreateRequest(items=[item0, item1])

        # ── act ──────────────────────────────────────────────────
        result = await service.create_parts_batch(payload)

        # ── assert ───────────────────────────────────────────────
        assert result.created == []
        assert len(result.failed) == 1
        assert result.failed[0].index == 0
        assert "customer '999'" in result.failed[0].message

        # No parts or events should be created since validation failed
        mock_parts.create.assert_not_called()
        mock_serial_counters.acquire_serial.assert_not_called()

    async def test_all_items_fail(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """All items fail validation → empty created list, all items in failed."""
        # ── arrange ──────────────────────────────────────────────
        mock_customers.get_by_id.side_effect = [None, None]

        item0 = PartCreateRequest(
            name="Bad A",
            drawing_no="DWGA",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='999',
        )
        item1 = PartCreateRequest(
            name="Bad B",
            drawing_no="DWGB",
            quantity=1,
            unit_price=Decimal("10"),
            request_date=date(2025, 1, 1),
            planned_delivery_date=date(2025, 2, 1), customer_id='888',
        )
        payload = PartBatchCreateRequest(items=[item0, item1])

        # ── act ──────────────────────────────────────────────────
        result = await service.create_parts_batch(payload)

        # ── assert ───────────────────────────────────────────────
        assert result.created == []
        assert len(result.failed) == 2
        assert result.failed[0].index == 0
        assert "customer '999'" in result.failed[0].message
        assert result.failed[1].index == 1
        assert "customer '888'" in result.failed[1].message

        mock_parts.create.assert_not_called()


# ======================================================================
# update_part
# ======================================================================


class TestUpdatePart:
    """Tests for PartService.update_part."""

    async def test_normal_update(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """Normal update → mutates fields, calls parts.update, returns PartOut."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10)
        mock_parts.get_by_id.return_value = part

        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        data = PartUpdateRequest(
            name="Updated Name",
            drawing_no="UPDATED-DWG",
            quantity=3,
            unit_price=Decimal("200"),
            total_price=Decimal("600"),
            is_urgent=True,
        )

        # ── act ──────────────────────────────────────────────────
        result = await service.update_part(1, data)

        # ── assert ───────────────────────────────────────────────
        mock_parts.get_by_id.assert_awaited_once_with(1)
        mock_parts.update.assert_awaited_once_with(part)

        assert part.name == "Updated Name"
        assert part.drawing_no == "UPDATED-DWG"
        assert part.quantity == 3
        assert part.unit_price == Decimal("200")
        assert part.total_price == Decimal("600")
        assert part.is_urgent is True

        assert isinstance(result, PartOut)
        assert result.id == 1

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """Part not found → BizError(BIZ_PART_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts.get_by_id.return_value = None
        data = PartUpdateRequest(name="New Name")

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.update_part(999, data)

        mock_parts.get_by_id.assert_awaited_once_with(999)
        mock_parts.update.assert_not_awaited()
        assert exc_info.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_customer_id_changed_not_found(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """customer_id changed but new customer not found → BizError(BIZ_CUSTOMER_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10)
        mock_parts.get_by_id.return_value = part

        mock_customers.get_by_id.return_value = None  # new customer doesn't exist

        data = PartUpdateRequest(customer_id='99')

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.update_part(1, data)

        mock_parts.get_by_id.assert_awaited_once_with(1)
        mock_customers.get_by_id.assert_awaited_once_with(99)
        mock_parts.update.assert_not_awaited()
        assert exc_info.value.code == ErrCode.BIZ_CUSTOMER_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_partial_update_only_some_fields(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """Only provided fields are updated; others remain unchanged."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10, name="Original")
        mock_parts.get_by_id.return_value = part

        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        data = PartUpdateRequest(name="Only Name Changed")  # only name

        # ── act ──────────────────────────────────────────────────
        result = await service.update_part(1, data)

        # ── assert ───────────────────────────────────────────────
        assert part.name == "Only Name Changed"
        assert part.drawing_no == "DWG-001"  # unchanged
        assert part.quantity == 1  # unchanged

    async def test_update_with_delivery_note_fields(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """PR-F (2026-07-17): update_part 透传 order_no / system_delivery_date / note。"""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10)
        mock_parts.get_by_id.return_value = part

        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        data = PartUpdateRequest(
            order_no="ORD-NEW-1",
            system_delivery_date=date(2026, 1, 15),
            note="系统交期变更",
        )

        # ── act ──────────────────────────────────────────────────
        await service.update_part(1, data)

        # ── assert ───────────────────────────────────────────────
        assert part.order_no == "ORD-NEW-1"
        assert part.system_delivery_date == date(2026, 1, 15)
        assert part.note == "系统交期变更"

    async def test_update_clear_note_with_explicit_none(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
    ) -> None:
        """note 显式传空字符串 → 清空（per CLAUDE.md update 语义：非 None 即更新）。"""
        part = _make_part(id=1, customer_id=10)
        part.note = "旧备注"
        mock_parts.get_by_id.return_value = part

        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        data = PartUpdateRequest(note="")
        await service.update_part(1, data)
        assert part.note == ""
        assert part.customer_id == 10  # unchanged
        assert part.is_urgent is False  # unchanged


# ======================================================================
# soft_delete_part
# ======================================================================


class TestSoftDeletePart:
    """Tests for PartService.soft_delete_part."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """Normal → calls parts.soft_delete."""
        # ── arrange ──────────────────────────────────────────────
        part = _make_part(id=1, customer_id=10)
        mock_parts.get_by_id.return_value = part

        # ── act ──────────────────────────────────────────────────
        result = await service.soft_delete_part(1)

        # ── assert ───────────────────────────────────────────────
        mock_parts.get_by_id.assert_awaited_once_with(1)
        mock_parts.soft_delete.assert_awaited_once_with(part)
        assert result is None

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """Part not found → BizError(BIZ_PART_NOT_FOUND, 404)."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.soft_delete_part(999)

        mock_parts.get_by_id.assert_awaited_once_with(999)
        mock_parts.soft_delete.assert_not_awaited()
        assert exc_info.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc_info.value.http_status == 404


# ======================================================================
# _to_out
# ======================================================================


class TestToOut:
    """Tests for PartService._to_out."""

    async def test_empty_list(
        self,
        service: PartService,
        mock_customers: AsyncMock,
        mock_workers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """Empty list → returns []."""
        # ── act ──────────────────────────────────────────────────
        result = await service._to_out([])

        # ── assert ───────────────────────────────────────────────
        assert result == []
        mock_customers.list_by_ids.assert_not_called()
        mock_workers.list_by_ids.assert_not_called()
        mock_shelves.list_by_ids.assert_not_called()

    async def test_normal_with_customer_path(
        self,
        service: PartService,
        mock_customers: AsyncMock,
        mock_workers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """Normal → batches customer lookups, returns PartOut list with customer_path."""
        # ── arrange ──────────────────────────────────────────────
        parent = _make_customer(id=1, name="ParentCorp")
        child = _make_customer(id=10, name="ChildCorp", parent_id=1)

        part_second = _make_part(id=100, customer_id=10)  # second-level customer
        part_first = _make_part(id=200, customer_id=1)  # first-level customer

        # _to_out calls list_by_ids twice: first for all cust_ids, then for parent_ids
        mock_customers.list_by_ids.return_value = [child, parent]

        # ── act ──────────────────────────────────────────────────
        result = await service._to_out([part_second, part_first])

        # ── assert ───────────────────────────────────────────────
        assert len(result) == 2

        # Second-level part: customer_path = "ParentCorp / ChildCorp"
        r1 = next(r for r in result if r.id == 100)
        assert r1.customer_name == "ChildCorp"
        assert r1.parent_customer_name == "ParentCorp"
        assert r1.customer_path == "ParentCorp / ChildCorp"

        # First-level part: customer_path = "ParentCorp"
        r2 = next(r for r in result if r.id == 200)
        assert r2.customer_name == "ParentCorp"
        assert r2.parent_customer_name is None
        assert r2.customer_path == "ParentCorp"

        # Worker and shelf lookups not needed for OFFICE location
        mock_workers.list_by_ids.assert_not_called()
        mock_shelves.list_by_ids.assert_not_called()

    async def test_part_with_worker_location(
        self,
        service: PartService,
        mock_customers: AsyncMock,
        mock_workers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """Part with WORKER location → includes worker_name in output."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        worker = _make_worker(id=501, name="WorkerWang")
        mock_workers.list_by_ids.return_value = [worker]

        part = _make_part(
            id=100, customer_id='10',
            location="WORKER",
            current_holder_id=501,
            status=PartStatus.IN_PROCESS.value,
        )

        # ── act ──────────────────────────────────────────────────
        result = await service._to_out([part])

        # ── assert ───────────────────────────────────────────────
        assert len(result) == 1
        r = result[0]
        assert r.location == "WORKER"
        assert r.worker_name == "WorkerWang"
        assert r.current_holder_kind == "worker"
        assert r.shelf_code is None

        mock_workers.list_by_ids.assert_awaited_once()

    async def test_part_with_shelf_location(
        self,
        service: PartService,
        mock_customers: AsyncMock,
        mock_workers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """Part with PRODUCTION_SHELF location → includes shelf_code in output."""
        # ── arrange ──────────────────────────────────────────────
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.list_by_ids.return_value = [cust]

        shelf = _make_shelf(id=301, code="PROD-B2")
        mock_shelves.list_by_ids.return_value = [shelf]

        part = _make_part(
            id=100, customer_id='10',
            location="PRODUCTION_SHELF",
            current_holder_id=301,
            status=PartStatus.IN_PROCESS.value,
        )

        # ── act ──────────────────────────────────────────────────
        result = await service._to_out([part])

        # ── assert ───────────────────────────────────────────────
        assert len(result) == 1
        r = result[0]
        assert r.location == "PRODUCTION_SHELF"
        assert r.current_holder_kind == "shelf"
        assert r.shelf_code == "PROD-B2"
        assert r.worker_name is None

        mock_shelves.list_by_ids.assert_awaited_once()


# ======================================================================
# list_pickable_parts / list_pickable_parts_all_shelves
# (2026-07-10 加 next_process_id IS NULL + shelf_ids 过滤；
#  仓库层承担 NULL 条件，业务短路逻辑不变)
# ======================================================================


class TestListPickablePartsSingleShelf:
    """扫码台 PICK_UP 单架版：/parts/by-work-type/{wt_id}?shelf_id=X。"""

    async def test_work_type_id_none_short_circuits(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
    ) -> None:
        """work_type_id=None → []，不查 wtp / 不查 parts。"""
        result = await service_with_wtp.list_pickable_parts(None, shelf_id=1)

        assert result == []
        mock_work_type_process.list_process_ids_by_work_type.assert_not_called()
        mock_parts.list_for_work_type.assert_not_called()

    async def test_no_mapped_processes_short_circuits(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
    ) -> None:
        """工种未映射任何工序 → []，不查 parts（让上层 UI 提示"工种映射为空"）。"""
        mock_work_type_process.list_process_ids_by_work_type.return_value = []

        result = await service_with_wtp.list_pickable_parts(work_type_id=7, shelf_id=1)

        assert result == []
        mock_work_type_process.list_process_ids_by_work_type.assert_awaited_once_with(
            7, include_deleted=False,
        )
        mock_parts.list_for_work_type.assert_not_called()

    async def test_happy_path_passes_to_repository(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """正常路径：service 拿到 process_ids 后透传给 repo（含 NULL 过滤由 repo 加）。"""
        mock_work_type_process.list_process_ids_by_work_type.return_value = [10, 20, 30]
        part = _make_part(id=100, customer_id=1, status=PartStatus.IN_PROCESS.value)
        mock_parts.list_for_work_type.return_value = [part]
        mock_customers.list_by_ids.return_value = []
        mock_shelves.list_by_ids.return_value = []

        result = await service_with_wtp.list_pickable_parts(
            work_type_id=7, shelf_id=42,
        )

        # service 必须把 (shelf_id, mapped_process_ids) 原样透传给 repo；
        # NULL 过滤在 repo 层处理（不在 service 入参里）。
        mock_parts.list_for_work_type.assert_awaited_once_with(
            shelf_id=42,
            mapped_process_ids=[10, 20, 30],
        )
        assert len(result) == 1
        assert result[0].id == 100  # _to_out 已把它包成 PartOut


class TestListPickablePartsAllShelves:
    """共享 HMI PICK_UP 跨架版：/parts/pickable-by-work-type/{wt_id}。

    2026-07-10 加：可选 shelf_ids 过滤（HMI scope），None = 全架。
    """

    async def test_work_type_id_none_short_circuits(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
    ) -> None:
        result = await service_with_wtp.list_pickable_parts_all_shelves(
            work_type_id=None, shelf_ids=[1, 2],
        )

        assert result == []
        mock_work_type_process.list_process_ids_by_work_type.assert_not_called()
        mock_parts.list_for_work_type_all_shelves.assert_not_called()

    async def test_no_mapped_processes_short_circuits(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
    ) -> None:
        mock_work_type_process.list_process_ids_by_work_type.return_value = []

        result = await service_with_wtp.list_pickable_parts_all_shelves(
            work_type_id=7, shelf_ids=[1, 2],
        )

        assert result == []
        mock_parts.list_for_work_type_all_shelves.assert_not_called()

    async def test_happy_path_without_shelf_filter(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """shelf_ids=None（默认）→ repo 拿到 None，不加 current_holder_id 过滤。"""
        mock_work_type_process.list_process_ids_by_work_type.return_value = [10, 20]
        mock_parts.list_for_work_type_all_shelves.return_value = []
        mock_customers.list_by_ids.return_value = []
        mock_shelves.list_by_ids.return_value = []

        result = await service_with_wtp.list_pickable_parts_all_shelves(
            work_type_id=7, shelf_ids=None,
        )

        mock_parts.list_for_work_type_all_shelves.assert_awaited_once_with(
            mapped_process_ids=[10, 20],
            shelf_ids=None,
        )
        assert result == []

    async def test_explicit_empty_shelf_ids_returns_empty(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
    ) -> None:
        """shelf_ids=[]（非 HMI 角色场景）→ 短路，repo 不查。"""
        mock_work_type_process.list_process_ids_by_work_type.return_value = [10, 20]

        result = await service_with_wtp.list_pickable_parts_all_shelves(
            work_type_id=7, shelf_ids=[],
        )

        # 空 list 必须短路 —— 非 HMI 角色不该看到任何零件
        assert result == []
        mock_parts.list_for_work_type_all_shelves.assert_not_called()

    async def test_explicit_shelf_ids_pass_through(
        self,
        service_with_wtp: PartService,
        mock_work_type_process: AsyncMock,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """shelf_ids=[1,2,3] → repo 拿到这个 list（HMI scope 过滤的入口）。"""
        mock_work_type_process.list_process_ids_by_work_type.return_value = [10, 20]
        part_a = _make_part(id=100, customer_id=1, current_holder_id=1)
        part_b = _make_part(id=101, customer_id=1, current_holder_id=2)
        mock_parts.list_for_work_type_all_shelves.return_value = [part_a, part_b]
        mock_customers.list_by_ids.return_value = []
        mock_shelves.list_by_ids.return_value = []

        result = await service_with_wtp.list_pickable_parts_all_shelves(
            work_type_id=7, shelf_ids=[1, 2, 3],
        )

        mock_parts.list_for_work_type_all_shelves.assert_awaited_once_with(
            mapped_process_ids=[10, 20],
            shelf_ids=[1, 2, 3],
        )
        assert len(result) == 2


class TestListPartsHeldByWorker:
    """扫码台 RETURN 新流程：/parts/by-worker/{worker_id}。

    2026-07-10 加：列出当前由某工人持有的所有零件（status=IN_PROCESS +
    location=WORKER + current_holder_id=worker_id）。
    """

    async def test_worker_id_zero_short_circuits(
        self,
        service_with_wtp: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """worker_id=0 → 返回 []，不查 repo。"""
        result = await service_with_wtp.list_parts_held_by_worker(worker_id=0)
        assert result == []
        mock_parts.list_held_by_worker.assert_not_called()

    async def test_worker_id_none_short_circuits(
        self,
        service_with_wtp: PartService,
        mock_parts: AsyncMock,
    ) -> None:
        """worker_id=None → 返回 []，不查 repo。"""
        result = await service_with_wtp.list_parts_held_by_worker(worker_id=None)  # type: ignore[arg-type]
        assert result == []
        mock_parts.list_held_by_worker.assert_not_called()

    async def test_happy_path_passes_worker_id_through(
        self,
        service_with_wtp: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_shelves: AsyncMock,
    ) -> None:
        """正常路径：worker_id=42 → repo.list_held_by_worker(worker_id=42)。"""
        mock_parts.list_held_by_worker.return_value = []
        mock_customers.list_by_ids.return_value = []
        mock_shelves.list_by_ids.return_value = []

        result = await service_with_wtp.list_parts_held_by_worker(worker_id=42)

        mock_parts.list_held_by_worker.assert_awaited_once_with(worker_id=42)
        assert result == []
