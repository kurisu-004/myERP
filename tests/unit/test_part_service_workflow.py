"""Unit tests for PartService workflow / state-transition methods.

Tests cover all state-transition endpoints (place_on_shelf, pick_up_by_scan,
scan_event, pass_inspection, deliver, complete, start_repair, complete_repair,
cancel) and the _check_parent_assembly helper.

All repository methods are mocked as AsyncMock. The TPart.sm property is
replaced with a plain MagicMock on the instance to avoid depending on the
real PartStateMachine.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.assembly import TAssembly
from model.enums import PartEventType, PartStatus, ShelfZone
from model.part import TPart
from model.shelf import TShelf
from model.worker import TWorker
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.worker import WorkerRepository
from schema.part import (
    PartOut,
    PartPickUpRequest,
    PartScanRequest,
    PlaceOnShelfRequest,
)
from service.part import PartService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_part(
    *,
    part_id: int = 1001,
    serial_no: str = "L0001",
    status: str = PartStatus.PENDING.value,
    location: str = "OFFICE",
    current_holder_id: int | None = None,
    assembly_id: int | None = None,
    customer_id: int = 1,
) -> MagicMock:
    """Build a mock TPart with the given attribute values.

    Uses ``MagicMock(spec=TPart)`` so all column fields and the read-only
    ``sm`` property can be freely overridden without needing a real DB
    session.  ``sm`` is pre-set to a ``MagicMock`` so callers can assert
    on its transition methods.
    """
    part = MagicMock(spec=TPart)
    part.id = part_id
    part.serial_no = serial_no
    part.name = "测试零件"
    part.drawing_no = "DWG-001"
    part.applicant_name = "张三"
    part.quantity = 10
    part.unit_price = Decimal("100.00")
    part.total_price = Decimal("1000.00")
    part.request_date = date(2026, 1, 1)
    part.planned_delivery_date = date(2026, 1, 15)
    part.actual_delivery_date = None
    part.status = status
    part.location = location
    part.is_urgent = False
    part.customer_id = customer_id
    part.current_holder_id = current_holder_id
    part.assembly_id = assembly_id
    part.placed_at = None
    part.sm = MagicMock()
    return part


def _make_shelf(
    *,
    shelf_id: int = 1,
    code: str = "PROD-A1",
    zone: str = ShelfZone.PRODUCTION.value,
    is_active: bool = True,
    deleted_at: datetime | None = None,
) -> MagicMock:
    """Build a mock TShelf.

    Uses ``MagicMock(spec=TShelf)`` to avoid SQLAlchemy ORM data
    descriptor conflicts on ``__new__``-only instances.
    """
    shelf = MagicMock(spec=TShelf)
    shelf.id = shelf_id
    shelf.code = code
    shelf.name = "生产货架A1"
    shelf.zone = zone
    shelf.location = None
    shelf.is_active = is_active
    shelf.deleted_at = deleted_at
    return shelf


def _make_worker(
    *,
    worker_id: int = 1,
    badge_code: str = "W001",
    name: str = "李四",
    is_active: bool = True,
) -> MagicMock:
    """Build a mock TWorker.

    Uses ``MagicMock(spec=TWorker)`` to avoid SQLAlchemy ORM data
    descriptor conflicts on ``__new__``-only instances.
    """
    worker = MagicMock(spec=TWorker)
    worker.id = worker_id
    worker.badge_code = badge_code
    worker.name = name
    worker.is_active = is_active
    return worker


def _make_part_out(**kwargs: object) -> MagicMock:
    """Build a minimal MagicMock that behaves like a PartOut.

    Defaults are set for fields that workflow methods access after
    ``_to_out`` returns.
    """
    out = MagicMock(spec=PartOut)
    out.id = kwargs.get("id", "1001")
    out.serial_no = kwargs.get("serial_no", "L0001")
    out.name = kwargs.get("name", "测试零件")
    out.customer_path = kwargs.get("customer_path", "Test / Path")
    out.status = kwargs.get("status", PartStatus.PENDING)
    out.assembly_id = kwargs.get("assembly_id", None)
    out.current_holder_kind = kwargs.get("current_holder_kind", None)
    out.shelf_code = kwargs.get("shelf_code", None)
    out.worker_name = kwargs.get("worker_name", None)
    out.location = kwargs.get("location", None)
    out.placed_at = kwargs.get("placed_at", None)
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_parts() -> PartRepository:
    """PartRepository with all used methods mocked as AsyncMock.

    Callers configure return values via ``mock_parts.get_by_id.return_value = …``
    etc.
    """
    repo = PartRepository.__new__(PartRepository)
    repo.get_by_id = AsyncMock()
    repo.get_by_serial = AsyncMock()
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    repo.soft_delete = AsyncMock()
    repo.list_children = AsyncMock()
    repo.session = AsyncMock()
    return repo


@pytest.fixture
def mock_customers() -> CustomerRepository:
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.list_by_ids = AsyncMock()
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def mock_workers() -> WorkerRepository:
    repo = WorkerRepository.__new__(WorkerRepository)
    repo.get_by_badge_code = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock()
    return repo


@pytest.fixture
def mock_events() -> PartEventRepository:
    repo = PartEventRepository.__new__(PartEventRepository)
    repo.create = AsyncMock()
    repo.list_by_part = AsyncMock()
    return repo


@pytest.fixture
def mock_serial_counters() -> SerialCounterRepository:
    repo = SerialCounterRepository.__new__(SerialCounterRepository)
    return repo


@pytest.fixture
def mock_shelves() -> ShelfRepository:
    repo = ShelfRepository.__new__(ShelfRepository)
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock()
    return repo


@pytest.fixture
def mock_processes():
    """模拟 ProcessRepository（用裸 MagicMock 避免依赖 ProcessRepository 的实现）。"""
    from repository.process import ProcessRepository
    repo = ProcessRepository.__new__(ProcessRepository)
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock()
    return repo


@pytest.fixture
def mock_work_types():
    from repository.work_type import WorkTypeRepository
    repo = WorkTypeRepository.__new__(WorkTypeRepository)
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def mock_work_type_process():
    from repository.work_type_process import WorkTypeProcessRepository
    repo = WorkTypeProcessRepository.__new__(WorkTypeProcessRepository)
    repo.list_process_ids_by_work_type = AsyncMock()
    return repo


@pytest.fixture
def service(
    mock_parts: PartRepository,
    mock_customers: CustomerRepository,
    mock_workers: WorkerRepository,
    mock_events: PartEventRepository,
    mock_serial_counters: SerialCounterRepository,
    mock_shelves: ShelfRepository,
    mock_processes,
    mock_work_types,
    mock_work_type_process,
) -> PartService:
    """PartService wired to mock repositories.

    ``broadcaster`` and ``event_broadcaster`` are set to ``None`` so those
    code paths are no-ops.  ``_to_out`` is also mocked to avoid touching
    real DB queries in serialization.
    """
    svc = PartService(
        parts=mock_parts,
        customers=mock_customers,
        workers=mock_workers,
        events=mock_events,
        serial_counters=mock_serial_counters,
        shelves=mock_shelves,
        processes=mock_processes,
        work_types=mock_work_types,
        work_type_process=mock_work_type_process,
        broadcaster=None,
        event_broadcaster=None,
    )
    svc._to_out = AsyncMock()
    return svc


# ===================================================================
# place_on_shelf
# ===================================================================


class TestPlaceOnShelf:
    """``PartService.place_on_shelf`` — PENDING → IN_PROCESS."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_events: PartEventRepository,
        mock_processes,
    ) -> None:
        """PENDING part placed on an active PRODUCTION shelf succeeds."""
        from model.process import TProcess
        part = _make_part()
        shelf = _make_shelf()
        process = TProcess(
            id=42, code="车", name="车床加工",
            category="INHOUSE", is_inspection=False, sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_processes.get_by_id.return_value = process
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]

        result = await service.place_on_shelf(
            1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42)
        )

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        mock_shelves.get_by_id.assert_awaited_once_with(1)
        mock_processes.get_by_id.assert_awaited_once_with(42)
        part.sm.place_on_shelf.assert_called_once_with(
            shelf=shelf, process=process, event_repo=mock_events
        )
        mock_parts.update.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """Missing part raises 404 before any shelf lookup."""
        mock_parts.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.place_on_shelf(999, PlaceOnShelfRequest(shelf_id=1, next_process_id=42))

        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND
        mock_parts.get_by_id.assert_awaited_once_with(999)

    async def test_shelf_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Non-existent shelf raises 404."""
        mock_parts.get_by_id.return_value = _make_part()
        mock_shelves.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.place_on_shelf(1001, PlaceOnShelfRequest(shelf_id=99, next_process_id=42))

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND
        mock_shelves.get_by_id.assert_awaited_once_with(99)

    async def test_shelf_deleted(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Shelf with a non-None deleted_at raises 404."""
        mock_parts.get_by_id.return_value = _make_part()
        mock_shelves.get_by_id.return_value = _make_shelf(
            deleted_at=datetime(2026, 6, 1)
        )

        with pytest.raises(BizError) as exc:
            await service.place_on_shelf(1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42))

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND

    async def test_shelf_inactive(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Inactive shelf raises 400."""
        mock_parts.get_by_id.return_value = _make_part()
        mock_shelves.get_by_id.return_value = _make_shelf(is_active=False)

        with pytest.raises(BizError) as exc:
            await service.place_on_shelf(1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42))

        assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_zone_not_production(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """INSPECTION zone shelf raises 400."""
        mock_parts.get_by_id.return_value = _make_part()
        mock_shelves.get_by_id.return_value = _make_shelf(
            zone=ShelfZone.INSPECTION.value
        )

        with pytest.raises(BizError) as exc:
            await service.place_on_shelf(1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42))

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST


# ===================================================================
# pick_up_by_scan
# ===================================================================


class TestPickUpByScan:
    """``PartService.pick_up_by_scan`` — IN_PROCESS (shelf) → IN_PROCESS (worker)."""

    @staticmethod
    def _default_part() -> TPart:
        return _make_part(
            status="IN_PROCESS",
            location="PRODUCTION_SHELF",
            current_holder_id=1,
        )

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
        mock_events: PartEventRepository,
    ) -> None:
        """Worker picks up a part from a PRODUCTION shelf."""
        part = self._default_part()
        shelf = _make_shelf()
        worker = _make_worker()
        mock_shelves.get_by_id.return_value = shelf
        mock_workers.get_by_badge_code.return_value = worker
        mock_parts.get_by_serial.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="W001")

        result = await service.pick_up_by_scan(data)

        mock_shelves.get_by_id.assert_awaited_once_with(1)
        mock_workers.get_by_badge_code.assert_awaited_once_with("W001")
        mock_parts.get_by_serial.assert_awaited_once_with("L0001")
        part.sm.pick_up.assert_called_once_with(
            worker=worker, shelf=shelf, event_repo=mock_events
        )
        mock_parts.update.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_shelf_not_found(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Non-existent shelf raises 404."""
        mock_shelves.get_by_id.return_value = None
        data = PartPickUpRequest(serial_no="L0001", shelf_id=99, badge_code="W001")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_shelf_zone_not_production(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
    ) -> None:
        """INSPECTION zone shelf raises 400."""
        mock_shelves.get_by_id.return_value = _make_shelf(
            zone=ShelfZone.INSPECTION.value
        )
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="W001")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_worker_not_found(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Badge code that doesn't match any worker raises 404."""
        mock_shelves.get_by_id.return_value = _make_shelf()
        mock_workers.get_by_badge_code.return_value = None
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="UNKNOWN")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_worker_inactive(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Inactive worker raises 400."""
        mock_shelves.get_by_id.return_value = _make_shelf()
        mock_workers.get_by_badge_code.return_value = _make_worker(is_active=False)
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="W001")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_WORKER_INACTIVE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_serial_no_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Serial number not matching any part raises 404."""
        mock_shelves.get_by_id.return_value = _make_shelf()
        mock_workers.get_by_badge_code.return_value = _make_worker()
        mock_parts.get_by_serial.return_value = None
        data = PartPickUpRequest(serial_no="XXXX", shelf_id=1, badge_code="W001")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_part_not_in_process(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Part not in IN_PROCESS status raises 400."""
        mock_shelves.get_by_id.return_value = _make_shelf()
        mock_workers.get_by_badge_code.return_value = _make_worker()
        mock_parts.get_by_serial.return_value = _make_part(
            status="PENDING", location="OFFICE"
        )
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="W001")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_TRANSITION
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_holder_mismatch(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Part held by a different shelf raises 400."""
        mock_shelves.get_by_id.return_value = _make_shelf(shelf_id=1)
        mock_workers.get_by_badge_code.return_value = _make_worker()
        mock_parts.get_by_serial.return_value = _make_part(
            status="IN_PROCESS",
            location="PRODUCTION_SHELF",
            current_holder_id=2,  # different shelf
        )
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="W001")

        with pytest.raises(BizError) as exc:
            await service.pick_up_by_scan(data)

        assert exc.value.code == ErrCode.BIZ_AUTH_SHELF_MISMATCH
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST


# ===================================================================
# scan_event
# ===================================================================


class TestScanEvent:
    """``PartService.scan_event`` — RETURNED or INSPECTED scan events."""

    # -- shared helpers ---------------------------------------------------

    @staticmethod
    def _default_worker_part() -> TPart:
        """Part currently held by worker 1 (IN_PROCESS + WORKER)."""
        p = _make_part(
            status="IN_PROCESS",
            location="WORKER",
            current_holder_id=1,
        )
        p.next_process_id = None  # MagicMock 默认不是 None,显式置空
        return p

    @staticmethod
    def _prod_shelf() -> TShelf:
        return _make_shelf(shelf_id=1, code="PROD-A1")

    @staticmethod
    def _insp_shelf() -> TShelf:
        return _make_shelf(
            shelf_id=2,
            code="INSP-I1",
            zone=ShelfZone.INSPECTION.value,
        )

    @staticmethod
    def _worker() -> TWorker:
        w = _make_worker(worker_id=1, badge_code="W001")
        w.work_type_id = None  # 显式置空,避免 MagicMock 默认值被 service 当作真实 id
        return w

    # -- common error cases (shared by RETURNED and INSPECTED) -----------

    async def test_shelf_not_found(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
    ) -> None:
        """Non-existent shelf raises 404 before worker lookup."""
        mock_shelves.get_by_id.return_value = None
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=99,
            badge_code="W001",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_worker_not_found(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Badge code unknown raises 404."""
        mock_shelves.get_by_id.return_value = self._prod_shelf()
        mock_workers.get_by_badge_code.return_value = None
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="UNKNOWN",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_worker_inactive(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Inactive worker raises 400."""
        mock_shelves.get_by_id.return_value = self._prod_shelf()
        mock_workers.get_by_badge_code.return_value = _make_worker(is_active=False)
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="W001",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_WORKER_INACTIVE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_invalid_event_type(
        self,
        service: PartService,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
        mock_parts: PartRepository,
    ) -> None:
        """Event type other than RETURNED or INSPECTED raises 400."""
        mock_shelves.get_by_id.return_value = self._prod_shelf()
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = self._default_worker_part()
        # Use model_construct to bypass Pydantic enum validation
        data = PartScanRequest.model_construct(
            serial_no="L0001",
            event_type="BOGUS_TYPE",
            shelf_id=1,
            badge_code="W001",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    # -- RETURNED --------------------------------------------------------

    async def test_returned_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
        mock_events: PartEventRepository,
        mock_processes,
    ) -> None:
        """Worker returns part to a PRODUCTION shelf."""
        from model.process import TProcess
        part = self._default_worker_part()
        shelf = self._prod_shelf()
        worker = self._worker()
        process = TProcess(
            id=99, code="铣", name="铣床加工",
            category="INHOUSE", is_inspection=False, sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_shelves.get_by_id.return_value = shelf
        mock_workers.get_by_badge_code.return_value = worker
        mock_parts.get_by_serial.return_value = part
        mock_processes.get_by_id.return_value = process
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="W001",
            next_process_id=99,
        )

        result = await service.scan_event(data)

        part.sm.return_to_shelf.assert_called_once_with(
            worker=worker, shelf=shelf, process=process,
            prev_process_code=None, worker_work_type_code=None,
            event_repo=mock_events,
        )
        mock_parts.update.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_returned_shelf_zone_not_production(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Returning to an INSPECTION zone shelf raises 400."""
        mock_shelves.get_by_id.return_value = _make_shelf(
            shelf_id=1,
            zone=ShelfZone.INSPECTION.value,
        )
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = self._default_worker_part()
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="W001",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_returned_part_not_held_by_worker(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Part held by a different worker raises 400."""
        mock_shelves.get_by_id.return_value = self._prod_shelf()
        mock_workers.get_by_badge_code.return_value = self._worker()
        # holder_id = 2, not worker 1
        mock_parts.get_by_serial.return_value = _make_part(
            status="IN_PROCESS",
            location="WORKER",
            current_holder_id=2,
        )
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="W001",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_AUTH_SHELF_MISMATCH
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_returned_part_not_in_process_worker(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Part not in IN_PROCESS/WORKER location raises 400."""
        mock_shelves.get_by_id.return_value = self._prod_shelf()
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = _make_part(
            status="IN_PROCESS",
            location="PRODUCTION_SHELF",  # wrong location for return
            current_holder_id=1,
        )
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="W001",
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_TRANSITION
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    # -- INSPECTED -------------------------------------------------------

    async def test_inspected_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
        mock_events: PartEventRepository,
    ) -> None:
        """Worker sends part to an INSPECTION shelf."""
        part = self._default_worker_part()
        shelf = self._prod_shelf()
        worker = self._worker()
        target = self._insp_shelf()
        mock_shelves.get_by_id.side_effect = [shelf, target]
        mock_workers.get_by_badge_code.return_value = worker
        mock_parts.get_by_serial.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.INSPECTED,
            shelf_id=1,
            badge_code="W001",
            target_inspection_shelf_id=2,
        )

        result = await service.scan_event(data)

        part.sm.inspect.assert_called_once_with(
            worker=worker, target_shelf=target, event_repo=mock_events
        )
        mock_parts.update.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_inspected_target_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Target inspection shelf not found raises 404."""
        mock_shelves.get_by_id.side_effect = [
            self._prod_shelf(),  # current shelf
            None,                # target not found
        ]
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = self._default_worker_part()
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.INSPECTED,
            shelf_id=1,
            badge_code="W001",
            target_inspection_shelf_id=99,
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_inspected_target_zone_not_inspection(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Target shelf not INSPECTION zone raises 400."""
        mock_shelves.get_by_id.side_effect = [
            self._prod_shelf(),
            _make_shelf(shelf_id=2, zone=ShelfZone.PRODUCTION.value),
        ]
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = self._default_worker_part()
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.INSPECTED,
            shelf_id=1,
            badge_code="W001",
            target_inspection_shelf_id=2,
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_inspected_target_inactive(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Inactive target shelf raises 400."""
        mock_shelves.get_by_id.side_effect = [
            self._prod_shelf(),
            _make_shelf(
                shelf_id=2,
                zone=ShelfZone.INSPECTION.value,
                is_active=False,
            ),
        ]
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = self._default_worker_part()
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.INSPECTED,
            shelf_id=1,
            badge_code="W001",
            target_inspection_shelf_id=2,
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_inspected_holder_not_worker(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """Part not held by the scanning worker raises 400."""
        mock_shelves.get_by_id.side_effect = [
            self._prod_shelf(),
            self._insp_shelf(),
        ]
        mock_workers.get_by_badge_code.return_value = self._worker()
        # holder_id = 2, not worker 1
        mock_parts.get_by_serial.return_value = _make_part(
            status="IN_PROCESS",
            location="WORKER",
            current_holder_id=2,
        )
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.INSPECTED,
            shelf_id=1,
            badge_code="W001",
            target_inspection_shelf_id=2,
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_AUTH_SHELF_MISMATCH
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_inspected_missing_target_shelf_id(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_workers: WorkerRepository,
    ) -> None:
        """INSPECTED without target_inspection_shelf_id raises 400."""
        mock_shelves.get_by_id.return_value = self._prod_shelf()
        mock_workers.get_by_badge_code.return_value = self._worker()
        mock_parts.get_by_serial.return_value = self._default_worker_part()
        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.INSPECTED,
            shelf_id=1,
            badge_code="W001",
            target_inspection_shelf_id=None,
        )

        with pytest.raises(BizError) as exc:
            await service.scan_event(data)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST


# ===================================================================
# pass_inspection
# ===================================================================


class TestPassInspection:
    """``PartService.pass_inspection`` — INSPECTION → READY_TO_SHIP."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(status="INSPECTION", location="INSPECTION_SHELF")
        mock_parts.get_by_id.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.pass_inspection(1001)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        part.sm.pass_inspection.assert_called_once_with(event_repo=mock_events)
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        mock_parts.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.pass_inspection(999)

        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND


# ===================================================================
# fail_inspection
# ===================================================================


class TestFailInspection:
    """``PartService.fail_inspection`` — INSPECTION → IN_PROCESS（打回生产货架）。

    与 complete_repair 形状一致：先校验 shelf 存在/active/zone=PRODUCTION，
    再清空 next_process_id（让文员重新下发时再选），最后调 sm.fail_inspection。
    """

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(
            status="INSPECTION", location="INSPECTION_SHELF",
        )
        # 模拟已有 next_process_id（应该被清空）
        part.next_process_id = 42
        shelf = _make_shelf()
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.fail_inspection(1001, shelf_id=1)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        mock_shelves.get_by_id.assert_awaited_once_with(1)
        assert part.next_process_id is None  # 清空
        part.sm.fail_inspection.assert_called_once_with(
            shelf=shelf, event_repo=mock_events,
        )
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        mock_parts.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(999, shelf_id=1)

        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_part_not_in_inspection(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """非 INSPECTION 状态的零件调用 fail_inspection → 400。"""
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = _make_shelf()

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(1001, shelf_id=1)

        assert exc.value.code == ErrCode.BIZ_INVALID_TRANSITION
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(1001, shelf_id=99)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_shelf_inactive(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = _make_shelf(is_active=False)

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(1001, shelf_id=1)

        assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_zone_not_production(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        """INSPECTION 区货架不允许打回（必须 PRODUCTION）。"""
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = _make_shelf(
            zone=ShelfZone.INSPECTION.value
        )

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(1001, shelf_id=1)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST


# ===================================================================
# deliver
# ===================================================================


class TestDeliver:
    """``PartService.deliver`` — READY_TO_SHIP → DELIVERED。

    PR-C（2026-07-10）新增司机扫码路径：
    - worker_badge_code=None → 文员手动调用，actual_delivery_date 写今天；
    - worker_badge_code=... → 校验 t_worker 工种 = '送货司机'；
      state machine on_deliver 接收 worker 参数并写 PartEvent.worker_id / badge_code。
    """

    @staticmethod
    def _driver_work_type():
        from model.work_type import TWorkType
        wt = MagicMock(spec=TWorkType)
        wt.id = 99
        wt.code = "送货司机"
        return wt

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.deliver(1001)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        part.sm.deliver.assert_called_once_with(
            worker=None, event_repo=mock_events,
        )
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        # actual_delivery_date 默认填今天
        assert part.actual_delivery_date == date.today()
        assert result is mock_out

    async def test_with_explicit_actual_delivery_date(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        service._to_out.return_value = [_make_part_out()]
        service._check_parent_assembly = AsyncMock()

        explicit = date(2026, 7, 9)
        await service.deliver(1001, actual_delivery_date=explicit)
        assert part.actual_delivery_date == explicit

    async def test_driver_badge_happy_path(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_workers: WorkerRepository,
        mock_work_types,
    ) -> None:
        """扫码台：worker_badge_code 命中送货司机 → 写入 worker_id / badge_code。"""
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        driver = _make_worker(worker_id=42, badge_code="18059214776")
        driver.work_type_id = 99
        mock_workers.get_by_badge_code.return_value = driver
        wt = self._driver_work_type()
        mock_work_types.get_by_id.return_value = wt
        service._to_out.return_value = [_make_part_out()]
        service._check_parent_assembly = AsyncMock()

        result = await service.deliver(1001, worker_badge_code="18059214776")

        mock_workers.get_by_badge_code.assert_awaited_once_with("18059214776")
        mock_work_types.get_by_id.assert_awaited_once_with(99)
        part.sm.deliver.assert_called_once_with(
            worker=driver, event_repo=mock_events if False else service.events,
        )
        assert part.actual_delivery_date == date.today()
        assert result is not None

    async def test_driver_badge_worker_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_workers: WorkerRepository,
        mock_work_types,
    ) -> None:
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        mock_workers.get_by_badge_code.return_value = None

        with pytest.raises(BizError) as exc:
            await service.deliver(1001, worker_badge_code="UNKNOWN")

        assert exc.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_driver_badge_worker_inactive(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_workers: WorkerRepository,
        mock_work_types,
    ) -> None:
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        mock_workers.get_by_badge_code.return_value = _make_worker(is_active=False)

        with pytest.raises(BizError) as exc:
            await service.deliver(1001, worker_badge_code="W001")

        assert exc.value.code == ErrCode.BIZ_WORKER_INACTIVE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_driver_badge_wrong_work_type(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_workers: WorkerRepository,
        mock_work_types,
    ) -> None:
        """非送货司机工种 → BIZ_INVALID_VALUE 400。"""
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        worker = _make_worker(badge_code="W001")
        worker.work_type_id = 7
        mock_workers.get_by_badge_code.return_value = worker

        from model.work_type import TWorkType
        bad_wt = MagicMock(spec=TWorkType)
        bad_wt.id = 7
        bad_wt.code = "车床"
        mock_work_types.get_by_id.return_value = bad_wt

        with pytest.raises(BizError) as exc:
            await service.deliver(1001, worker_badge_code="W001")

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST
        assert "送货司机" in exc.value.message


# ===================================================================
# complete
# ===================================================================


class TestComplete:
    """``PartService.complete`` — DELIVERED → COMPLETED."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(status="DELIVERED")
        mock_parts.get_by_id.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.complete(1001)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        part.sm.complete.assert_called_once_with(event_repo=mock_events)
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out


# ===================================================================
# start_repair
# ===================================================================


class TestStartRepair:
    """``PartService.start_repair`` — any non-terminal → REPAIRING."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.start_repair(1001)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        part.sm.start_repair.assert_called_once_with(event_repo=mock_events)
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out


# ===================================================================
# complete_repair
# ===================================================================


class TestCompleteRepair:
    """``PartService.complete_repair`` — REPAIRING → IN_PROCESS."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(status="REPAIRING")
        shelf = _make_shelf()
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.complete_repair(1001, shelf_id=1)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        mock_shelves.get_by_id.assert_awaited_once_with(1)
        part.sm.complete_repair.assert_called_once_with(
            shelf=shelf, event_repo=mock_events
        )
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_shelf_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        mock_parts.get_by_id.return_value = _make_part(status="REPAIRING")
        mock_shelves.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.complete_repair(1001, shelf_id=99)

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_shelf_inactive(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        mock_parts.get_by_id.return_value = _make_part(status="REPAIRING")
        mock_shelves.get_by_id.return_value = _make_shelf(is_active=False)

        with pytest.raises(BizError) as exc:
            await service.complete_repair(1001, shelf_id=1)

        assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_zone_not_production(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
    ) -> None:
        mock_parts.get_by_id.return_value = _make_part(status="REPAIRING")
        mock_shelves.get_by_id.return_value = _make_shelf(
            zone=ShelfZone.INSPECTION.value
        )

        with pytest.raises(BizError) as exc:
            await service.complete_repair(1001, shelf_id=1)

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST


# ===================================================================
# cancel
# ===================================================================


class TestCancel:
    """``PartService.cancel`` — any non-terminal → CANCELLED."""

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(status="IN_PROCESS", location="PRODUCTION_SHELF")
        mock_parts.get_by_id.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.cancel(1001)

        mock_parts.get_by_id.assert_awaited_once_with(1001)
        part.sm.cancel.assert_called_once_with(event_repo=mock_events)
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out


# ===================================================================
# _check_parent_assembly
# ===================================================================


class TestCheckParentAssembly:
    """``PartService._check_parent_assembly`` — auto-update Assembly status."""

    async def test_no_assembly_id_noop(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """part.assembly_id is None → returns without touching session."""
        part = _make_part(assembly_id=None)

        await service._check_parent_assembly(part)

        # session and list_children must NOT have been touched.
        mock_parts.session.execute.assert_not_called()
        mock_parts.list_children.assert_not_called()

    async def test_assembly_complete(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """All non-cancelled children COMPLETED → assembly.sm.complete()."""
        part = _make_part(assembly_id=123, status="COMPLETED")

        # Mock the session so it returns an assembly with status "IN_PROCESS"
        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "IN_PROCESS"
        mock_assembly.sm = MagicMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        # All non-cancelled children are COMPLETED
        child_completed_1 = _make_part(
            part_id=2001, status="COMPLETED", assembly_id=123
        )
        child_completed_2 = _make_part(
            part_id=2002, status="COMPLETED", assembly_id=123
        )
        # A cancelled child should be ignored
        child_cancelled = _make_part(
            part_id=2003, status="CANCELLED", assembly_id=123
        )
        mock_parts.list_children = AsyncMock(
            return_value=[child_completed_1, child_completed_2, child_cancelled]
        )

        await service._check_parent_assembly(part)

        mock_session.execute.assert_awaited_once()
        mock_parts.list_children.assert_awaited_once_with(123)
        mock_assembly.sm.complete.assert_called_once()


# ===================================================================
# release_from_programming — 2026-07-10 起加文件前置校验
# ===================================================================


class TestReleaseFromProgrammingPrerequisites:
    """下发到 CNC 货架必须先上传 ≥1 G 代码 + ≥1 CNC 设定单。"""

    async def test_no_g_code_raises(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_processes,
    ) -> None:
        from schema.part import PlaceOnShelfRequest

        part = _make_part(status="PROGRAMMING")
        mock_parts.get_by_id.return_value = part
        # G 代码缺失
        service.files = MagicMock()
        service.files.list_by_part = AsyncMock(return_value=[])  # 都没有

        with pytest.raises(BizError) as exc:
            await service.release_from_programming(
                part_id=1001,
                data=PlaceOnShelfRequest(shelf_id=1, next_process_id=42),
            )
        assert exc.value.code == ErrCode.BIZ_CNC_PROGRAM_REQUIRED
        assert exc.value.http_status == 400

    async def test_no_setup_sheet_raises(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_processes,
    ) -> None:
        from schema.part import PlaceOnShelfRequest

        part = _make_part(status="PROGRAMMING")
        mock_parts.get_by_id.return_value = part
        # G 代码有，设定单没有
        g_code_mock = MagicMock()
        g_code_mock.kind = "G_CODE"
        service.files = MagicMock()
        service.files.list_by_part = AsyncMock(
            side_effect=lambda pid, kind=None: [g_code_mock] if kind == "G_CODE" else []
        )

        with pytest.raises(BizError) as exc:
            await service.release_from_programming(
                part_id=1001,
                data=PlaceOnShelfRequest(shelf_id=1, next_process_id=42),
            )
        assert exc.value.code == ErrCode.BIZ_CNC_SETUP_SHEET_REQUIRED
        assert exc.value.http_status == 400

    async def test_with_both_files_succeeds(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_processes,
    ) -> None:
        from schema.part import PlaceOnShelfRequest
        from model.process import TProcess

        part = _make_part(status="PROGRAMMING")
        shelf = _make_shelf()
        process = TProcess(
            id=42, code="车", name="车床加工",
            category="INHOUSE", is_inspection=False, sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_processes.get_by_id.return_value = process
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]

        # G 代码 + 设定单都有
        g_code_mock = MagicMock()
        g_code_mock.kind = "G_CODE"
        setup_mock = MagicMock()
        setup_mock.kind = "SETUP_SHEET"
        service.files = MagicMock()
        service.files.list_by_part = AsyncMock(
            side_effect=lambda pid, kind=None: {
                "G_CODE": [g_code_mock],
                "SETUP_SHEET": [setup_mock],
            }.get(kind, [])
        )

        result = await service.release_from_programming(
            part_id=1001,
            data=PlaceOnShelfRequest(shelf_id=1, next_process_id=42),
        )
        assert result == mock_out
        # 状态机 release_from_programming 被调
        part.sm.release_from_programming.assert_called_once()
