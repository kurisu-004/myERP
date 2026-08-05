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
from model.enums import PartEventType, PartLocation, PartStatus, ProcessCategory, ShelfZone
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
    FailInspectionRequest,
    PartOut,
    PartPickUpRequest,
    PartScanRequest,
    PlaceOnShelfRequest,
)
from service.part import PartService
from tests.unit._fake_batches import FakePartBatchRepository

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
    next_process_id: int | None = None,
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
    part.next_process_id = next_process_id
    part.version = 0  # OCC 校验需要（2026-07-28）
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
    worker.work_type_id = None  # 避免 MagicMock 默认值触发工种上限校验
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
    repo.session = MagicMock()  # PR-H 2026-07-29：refresh_for_state_machine 需要 session
    repo.create = AsyncMock()
    repo.list_by_part = AsyncMock()
    repo.latest_sent_to_outsource_for_part = AsyncMock(return_value=None)
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
    repo.get_default_process_id_for_work_type = AsyncMock()
    return repo


@pytest.fixture
def mock_shelf_process_repo():
    """ShelfProcessRepository mock（2026-07-17 起 PartService 强制校验）。"""
    from repository.shelf_process import ShelfProcessRepository
    repo = ShelfProcessRepository.__new__(ShelfProcessRepository)
    repo.list_process_ids_by_shelf = AsyncMock(return_value=[])
    repo.list_all_mappings = AsyncMock(return_value={})
    # PR-H 2026-07-28：外协发送统一走 OUTSOURCE-bound 货架闸门
    repo.list_shelf_ids_with_process_category = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_outsource_companies():
    from repository.outsource_company import OutsourceCompanyRepository
    repo = OutsourceCompanyRepository.__new__(OutsourceCompanyRepository)
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_outsource_company_process():
    from repository.outsource_company_process import OutsourceCompanyProcessRepository
    repo = OutsourceCompanyProcessRepository.__new__(OutsourceCompanyProcessRepository)
    repo.list_process_ids_by_outsource_company = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_outsource_quotes():
    from repository.outsource_quote import OutsourceQuoteRepository
    repo = OutsourceQuoteRepository.__new__(OutsourceQuoteRepository)
    repo.session = MagicMock()  # PR-H 2026-07-29：refresh_for_state_machine 需要 session
    repo.get_one_approved = AsyncMock(return_value=None)
    repo.get_approved_for_part_process = AsyncMock(return_value=None)
    repo.get_one_active_for_tuple = AsyncMock(return_value=None)
    repo.find_active_for_part_company_process = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=lambda q: q)
    repo.update = AsyncMock(side_effect=lambda q: q)
    return repo


@pytest.fixture
def mock_outsource_shipments():
    from repository.outsource_shipment import OutsourceShipmentRepository
    repo = OutsourceShipmentRepository.__new__(OutsourceShipmentRepository)
    repo.session = MagicMock()
    repo.create = AsyncMock(side_effect=lambda s: s)
    repo.update = AsyncMock(side_effect=lambda s: s)
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_open_by_batch_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_quote_events():
    from repository.outsource_quote_event import OutsourceQuoteEventRepository
    repo = OutsourceQuoteEventRepository.__new__(OutsourceQuoteEventRepository)
    repo.add = MagicMock(side_effect=lambda ev: ev)
    repo.create = AsyncMock(side_effect=lambda ev: ev)
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
    mock_shelf_process_repo,
    mock_outsource_companies,
    mock_outsource_company_process,
    mock_outsource_quotes,
    mock_quote_events,
    mock_outsource_shipments,
) -> PartService:
    """PartService wired to mock repositories.

    ``broadcaster`` and ``event_broadcaster`` are set to ``None`` so those
    code paths are no-ops.  ``_to_out`` is also mocked to avoid touching
    real DB queries in serialization.
    """
    svc = PartService(
        parts=mock_parts,
        part_batches=FakePartBatchRepository(
            # 测试常在 fixture 后重绑 get_by_id/get_by_serial → 调用时再解析。
            parts_provider=FakePartBatchRepository.chain_attrs(
                mock_parts, "get_by_id", "get_by_serial",
            ),
        ),
        customers=mock_customers,
        workers=mock_workers,
        events=mock_events,
        serial_counters=mock_serial_counters,
        shelves=mock_shelves,
        processes=mock_processes,
        work_types=mock_work_types,
        work_type_process=mock_work_type_process,
        shelf_process_repo=mock_shelf_process_repo,
        outsource_companies=mock_outsource_companies,
        outsource_company_process=mock_outsource_company_process,
        outsource_quotes=mock_outsource_quotes,
        quote_events=mock_quote_events,
        outsource_shipments=mock_outsource_shipments,
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
        mock_shelf_process_repo,
    ) -> None:
        """PENDING part placed on an active PRODUCTION shelf succeeds."""
        from model.process import TProcess
        part = _make_part()
        shelf = _make_shelf()
        process = TProcess(
            id=42, code="车", name="车床加工",
            category="INHOUSE", sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_processes.get_by_id.return_value = process
        # 2026-07-17：上架要求货架已映射该工序
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [42]
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        result = await service.place_on_shelf(
            1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42)
        )

        mock_parts.get_by_id.assert_awaited_with(1001)
        mock_shelves.get_by_id.assert_awaited_with(1)
        mock_processes.get_by_id.assert_awaited_with(42)
        part.sm.place_on_shelf.assert_called_once_with(
            shelf=shelf, process=process, event_repo=mock_events,
            created_by=None,
        )
        mock_parts.update.assert_awaited_once_with(part)
        # 回归：上架（PENDING→IN_PROCESS）必须触发父装配件状态检查
        service._check_parent_assembly.assert_awaited_once_with(part)
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
        mock_parts.get_by_id.assert_awaited_with(999)

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
        mock_shelves.get_by_id.assert_awaited_with(99)

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
        worker.work_type_id = None  # 避免 MagicMock 默认值触发工种上限校验
        mock_shelves.get_by_id.return_value = shelf
        mock_workers.get_by_badge_code.return_value = worker
        mock_parts.get_by_serial.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        data = PartPickUpRequest(serial_no="L0001", shelf_id=1, badge_code="W001")

        result = await service.pick_up_by_scan(data)

        mock_shelves.get_by_id.assert_awaited_with(1)
        mock_workers.get_by_badge_code.assert_awaited_once_with("W001")
        mock_parts.get_by_serial.assert_any_await("L0001")  # 批次化：fake provider 另有一次 int 调用
        part.sm.pick_up.assert_called_once_with(
            worker=worker, shelf=shelf, event_repo=mock_events,
            created_by=None,
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
        mock_shelf_process_repo,
    ) -> None:
        """Worker returns part to a PRODUCTION shelf."""
        from model.process import TProcess
        part = self._default_worker_part()
        shelf = self._prod_shelf()
        worker = self._worker()
        process = TProcess(
            id=99, code="铣", name="铣床加工",
            category="INHOUSE", sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_shelves.get_by_id.return_value = shelf
        mock_workers.get_by_badge_code.return_value = worker
        mock_parts.get_by_serial.return_value = part
        mock_processes.get_by_id.return_value = process
        # 2026-07-17：放回要求货架已映射该工序
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [99]
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
            created_by=None,
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
            worker=worker, target_shelf=target, event_repo=mock_events,
            created_by=None,
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

        mock_parts.get_by_id.assert_awaited_with(1001)
        part.sm.pass_inspection.assert_called_once_with(
            event_repo=mock_events, created_by=None,
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
            await service.pass_inspection(999)

        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND


# ===================================================================
# fail_inspection
# ===================================================================


class TestFailInspection:
    """``PartService.fail_inspection`` — INSPECTION → IN_PROCESS（打回生产货架）。

    2026-07-21 改：
    - 入参改为 ``FailInspectionRequest``（shelf_id + next_process_id + note）。
    - 货架 + 工序 + 映射校验统一走 ``_validate_production_shelf_and_process``
      （与 ``place_on_shelf`` / ``release_from_programming`` 对齐）。
    - **保留** ``part.next_process_id``（不再清空；保留 inspector 指定的下一道工序）。
    - ``note`` 通过 ``sm.fail_inspection(..., note=...)`` 透传到 ``on_fail_inspection``
      回调，写入 ``t_part_event.note``。
    """

    async def test_normal(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        part = _make_part(
            status="INSPECTION", location="INSPECTION_SHELF",
        )
        # 模拟已有 next_process_id（应该被覆盖为 inspector 指定的）
        part.next_process_id = 42
        shelf = _make_shelf()
        # 构造一个 mock 工序对象；本测试只关心 next_process_id 被设为什么，
        # 不进入 _validate_production_shelf_and_process 的真实校验
        process_obj = MagicMock()
        process_obj.id = 100
        service._validate_production_shelf_and_process = AsyncMock(
            return_value=(shelf, process_obj),
        )
        mock_parts.get_by_id.return_value = part
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]
        service._check_parent_assembly = AsyncMock()

        payload = FailInspectionRequest(
            shelf_id="1", next_process_id="100",
            note="尺寸超差需返修",
        )
        result = await service.fail_inspection(1001, payload)

        mock_parts.get_by_id.assert_awaited_with(1001)
        service._validate_production_shelf_and_process.assert_awaited_once_with(1, 100)
        assert part.next_process_id == 100  # 保留（覆盖原有 42）
        part.sm.fail_inspection.assert_called_once_with(
            shelf=shelf, process=process_obj, event_repo=mock_events,
            created_by=None, note="尺寸超差需返修",
        )
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out

    async def test_normal_no_note(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_events: PartEventRepository,
    ) -> None:
        """不填备注时 note=None 透传（保持兼容旧行为）。"""
        part = _make_part(status="INSPECTION")
        shelf = _make_shelf()
        process_obj = MagicMock()
        process_obj.id = 100
        service._validate_production_shelf_and_process = AsyncMock(
            return_value=(shelf, process_obj),
        )
        mock_parts.get_by_id.return_value = part
        service._to_out.return_value = [_make_part_out()]
        service._check_parent_assembly = AsyncMock()

        payload = FailInspectionRequest(
            shelf_id="1", next_process_id="100", note=None,
        )
        await service.fail_inspection(1001, payload)

        part.sm.fail_inspection.assert_called_once_with(
            shelf=shelf, process=process_obj, event_repo=mock_events,
            created_by=None, note=None,
        )

    async def test_part_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        mock_parts.get_by_id.return_value = None

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(
                999,
                FailInspectionRequest(
                    shelf_id="1", next_process_id="100", note=None,
                ),
            )

        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_part_not_in_inspection(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """非 INSPECTION 状态的零件调用 fail_inspection → 400。"""
        part = _make_part(status="READY_TO_SHIP")
        mock_parts.get_by_id.return_value = part
        # 不需要 mock _validate_production_shelf_and_process —— 状态校验先于它

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(
                1001,
                FailInspectionRequest(
                    shelf_id="1", next_process_id="100", note=None,
                ),
            )

        assert exc.value.code == ErrCode.BIZ_INVALID_TRANSITION
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_not_found(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """_validate_production_shelf_and_process 抛 BIZ_SHELF_NOT_FOUND → 直接透传。"""
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        service._validate_production_shelf_and_process = AsyncMock(
            side_effect=BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message="shelf 99 not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            ),
        )

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(
                1001,
                FailInspectionRequest(
                    shelf_id="99", next_process_id="100", note=None,
                ),
            )

        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_shelf_inactive(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        service._validate_production_shelf_and_process = AsyncMock(
            side_effect=BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message="shelf inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ),
        )

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(
                1001,
                FailInspectionRequest(
                    shelf_id="1", next_process_id="100", note=None,
                ),
            )

        assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_zone_not_production(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """INSPECTION 区货架不允许打回（必须 PRODUCTION）。"""
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        service._validate_production_shelf_and_process = AsyncMock(
            side_effect=BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="shelf not PRODUCTION",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ),
        )

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(
                1001,
                FailInspectionRequest(
                    shelf_id="1", next_process_id="100", note=None,
                ),
            )

        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_shelf_process_not_mapped(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """shelf↔process 缺映射 → 422（与 place_on_shelf / release_from_programming 一致）。"""
        part = _make_part(status="INSPECTION")
        mock_parts.get_by_id.return_value = part
        service._validate_production_shelf_and_process = AsyncMock(
            side_effect=BizError(
                code=ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED,
                message="shelf A 未配置工序 X",
                http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            ),
        )

        with pytest.raises(BizError) as exc:
            await service.fail_inspection(
                1001,
                FailInspectionRequest(
                    shelf_id="1", next_process_id="100", note=None,
                ),
            )

        assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED
        assert exc.value.http_status == http_status.HTTP_422_UNPROCESSABLE_ENTITY


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

        mock_parts.get_by_id.assert_awaited_with(1001)
        part.sm.deliver.assert_called_once_with(
            worker=None, event_repo=mock_events,
            created_by=None,
        )
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out
        # 2026-07-29 批次化：actual_delivery_date 由 rollup 在全部活跃批次
        # DELIVERED 时写入（mock sm 不翻转状态，这里显式模拟后验证 rollup）。
        batch = next(iter(service.part_batches.store.values()))
        batch.status = "DELIVERED"
        await service._rollup_part_status(part)
        assert part.actual_delivery_date == date.today()

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
        mock_work_types.get_by_id.assert_awaited_with(99)
        part.sm.deliver.assert_called_once_with(
            worker=driver, event_repo=mock_events if False else service.events,
            created_by=None,
        )
        # 2026-07-29 批次化：actual_delivery_date 由 rollup 写入（mock sm 不翻转
        # 状态，这里显式模拟后验证 rollup）。
        batch = next(iter(service.part_batches.store.values()))
        batch.status = "DELIVERED"
        await service._rollup_part_status(part)
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

        mock_parts.get_by_id.assert_awaited_with(1001)
        part.sm.complete.assert_called_once_with(
            event_repo=mock_events, created_by=None,
        )
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

        mock_parts.get_by_id.assert_awaited_with(1001)
        part.sm.start_repair.assert_called_once_with(
            event_repo=mock_events, created_by=None,
        )
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

        mock_parts.get_by_id.assert_awaited_with(1001)
        mock_shelves.get_by_id.assert_awaited_with(1)
        part.sm.complete_repair.assert_called_once_with(
            shelf=shelf, event_repo=mock_events,
            created_by=None,
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
            zone="OFFICE"
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

        mock_parts.get_by_id.assert_awaited_with(1001)
        part.sm.cancel.assert_called_once_with(
            event_repo=mock_events, created_by=None,
        )
        mock_parts.update.assert_awaited_once_with(part)
        service._check_parent_assembly.assert_awaited_once_with(part)
        assert result is mock_out


# ===================================================================
# _check_parent_assembly
# ===================================================================


class TestCheckParentAssembly:
    """``PartService._check_parent_assembly`` — auto-update Assembly status.

    2026-08-03：内部委托给 ``service/_assembly_rollup.recompute_assembly_status``，
    该 helper 走 ``assembly.sm.recompute(target)``（任意方向，含回退）。
    测试期望从 ``sm.start_production()`` / ``sm.complete()`` 改为
    ``sm.recompute("IN_PROCESS")`` / ``sm.recompute("COMPLETED")`` 等。
    """

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
        """All non-cancelled children COMPLETED → assembly.sm.recompute("COMPLETED")."""
        part = _make_part(assembly_id=123, status="COMPLETED")

        # Mock the session so it returns an assembly with status "DELIVERED"
        # （与装配体进入 COMPLETED 前一阶段对齐；最落后非取消子件 COMPLETED → COMPLETED）
        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "DELIVERED"
        mock_assembly.sm = MagicMock()
        # recompute 返回 True 表示状态实际改变
        mock_assembly.sm.recompute.return_value = True

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
        mock_assembly.sm.recompute.assert_called_once_with("COMPLETED")

    async def test_assembly_start_production(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """PENDING assembly + any child left PENDING → IN_PROCESS.

        回归：这是本次 bug 的核心分支——子件上货架进入 IN_PROCESS 后，
        父装配件必须从 PENDING 推进到 IN_PROCESS。
        """
        part = _make_part(assembly_id=123, status="IN_PROCESS")

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "PENDING"
        mock_assembly.sm = MagicMock()
        mock_assembly.sm.recompute.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        # 一个子件已 IN_PROCESS，另一个仍 PENDING（尚未全部离开 PENDING）
        child_in_process = _make_part(
            part_id=2001, status="IN_PROCESS", assembly_id=123
        )
        child_pending = _make_part(
            part_id=2002, status="PENDING", assembly_id=123
        )
        mock_parts.list_children = AsyncMock(
            return_value=[child_in_process, child_pending]
        )

        await service._check_parent_assembly(part)

        mock_parts.list_children.assert_awaited_once_with(123)
        # 最落后子件为 PENDING → target 也是 PENDING == current → no-op
        # （之前测试断言 start_production；现在的 helper 判定 PENDING < IN_PROCESS
        #  都会收敛到 PENDING，但 PENDING == PENDING 是 no-op 不调 recompute。
        #  所以此处真实路径：list_children → least=PENDING → target=PENDING →
        #  == current → recompute 不被调用。这条用例实际验证了 no-op 路径，
        #  保留以锁住行为。）
        mock_assembly.sm.recompute.assert_not_called()

    async def test_assembly_pending_all_children_pending_noop(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """PENDING assembly + all children still PENDING → no transition."""
        part = _make_part(assembly_id=123, status="PENDING")

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "PENDING"
        mock_assembly.sm = MagicMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        mock_parts.list_children = AsyncMock(return_value=[
            _make_part(part_id=2001, status="PENDING", assembly_id=123),
            _make_part(part_id=2002, status="PENDING", assembly_id=123),
        ])

        await service._check_parent_assembly(part)

        mock_assembly.sm.recompute.assert_not_called()

    # ===== 2026-08-03 新增：7 态派生态 =====

    async def test_assembly_enter_inspection(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """子件最少进度为 INSPECTION → 父件 recompute("INSPECTION")。"""
        part = _make_part(assembly_id=123, status="INSPECTION")

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "IN_PROCESS"
        mock_assembly.sm = MagicMock()
        mock_assembly.sm.recompute.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        # 最落后子件 INSPECTION，另一个已 READY_TO_SHIP
        mock_parts.list_children = AsyncMock(return_value=[
            _make_part(part_id=2001, status="INSPECTION", assembly_id=123),
            _make_part(part_id=2002, status="READY_TO_SHIP", assembly_id=123),
        ])

        await service._check_parent_assembly(part)

        mock_assembly.sm.recompute.assert_called_once_with("INSPECTION")

    async def test_assembly_ready_to_ship(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """子件最少进度为 READY_TO_SHIP → 父件 recompute("READY_TO_SHIP")。"""
        part = _make_part(assembly_id=123, status="READY_TO_SHIP")

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "INSPECTION"
        mock_assembly.sm = MagicMock()
        mock_assembly.sm.recompute.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        mock_parts.list_children = AsyncMock(return_value=[
            _make_part(part_id=2001, status="READY_TO_SHIP", assembly_id=123),
            _make_part(part_id=2002, status="DELIVERED", assembly_id=123),
        ])

        await service._check_parent_assembly(part)

        mock_assembly.sm.recompute.assert_called_once_with("READY_TO_SHIP")

    async def test_assembly_delivered(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """所有非取消子件都 DELIVERED → 父件 recompute("DELIVERED")。"""
        part = _make_part(assembly_id=123, status="DELIVERED")

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "READY_TO_SHIP"
        mock_assembly.sm = MagicMock()
        mock_assembly.sm.recompute.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        mock_parts.list_children = AsyncMock(return_value=[
            _make_part(part_id=2001, status="DELIVERED", assembly_id=123),
            _make_part(part_id=2002, status="DELIVERED", assembly_id=123),
        ])

        await service._check_parent_assembly(part)

        mock_assembly.sm.recompute.assert_called_once_with("DELIVERED")

    async def test_assembly_backward_regression(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """回退：父件在 INSPECTION，子件 fail_inspection → ON_SHELF →
        父件 recompute("IN_PROCESS")（含 BACKWARD regression）。
        """
        part = _make_part(assembly_id=123, status="IN_PROCESS")  # 子件已回退

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "INSPECTION"  # 父件还在 INSPECTION
        mock_assembly.sm = MagicMock()
        mock_assembly.sm.recompute.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        # fail_inspection → ON_SHELF → DB status = IN_PROCESS
        mock_parts.list_children = AsyncMock(return_value=[
            _make_part(part_id=2001, status="IN_PROCESS", assembly_id=123),
            _make_part(part_id=2002, status="INSPECTION", assembly_id=123),
        ])

        await service._check_parent_assembly(part)

        mock_assembly.sm.recompute.assert_called_once_with("IN_PROCESS")

    async def test_terminal_assembly_no_change(
        self,
        service: PartService,
        mock_parts: PartRepository,
    ) -> None:
        """终态父件（COMPLETED / CANCELLED）拒绝改写（SM 短路）。"""
        part = _make_part(assembly_id=123, status="IN_PROCESS")

        mock_assembly = MagicMock(spec=TAssembly)
        mock_assembly.id = 123
        mock_assembly.status = "COMPLETED"  # 终态
        mock_assembly.sm = MagicMock()
        mock_assembly.sm.recompute.return_value = False  # SM 终态短路

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_assembly
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_parts.session = mock_session

        mock_parts.list_children = AsyncMock(return_value=[
            _make_part(part_id=2001, status="IN_PROCESS", assembly_id=123),
        ])

        await service._check_parent_assembly(part)

        mock_assembly.sm.recompute.assert_not_called()


# ===================================================================
# release_from_programming —— 2026-07-10 起加文件前置校验，
# 2026-08-02 起又移除该前置校验：release 不再要求 G 代码 / 设定单。
# ===================================================================


class TestReleaseFromProgrammingPrerequisites:
    """下发到 CNC 货架不再做文件前置校验（2026-08-02 移除）。"""

    async def test_release_without_any_files_succeeds(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        """完全没上传任何 CNC 文件也可以 release（前置校验已移除）。"""
        from schema.part import PlaceOnShelfRequest
        from model.process import TProcess

        part = _make_part(status="PROGRAMMING")
        shelf = _make_shelf()
        process = TProcess(
            id=42, code="车", name="车床加工",
            category="INHOUSE", sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_processes.get_by_id.return_value = process
        # 2026-07-17：下发要求货架已映射该工序
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [42]
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]

        # 完全没上传任何 CNC 文件
        service.files = MagicMock()
        service.files.list_by_part = AsyncMock(return_value=[])

        result = await service.release_from_programming(
            part_id=1001,
            data=PlaceOnShelfRequest(shelf_id=shelf.id, next_process_id=process.id),
        )
        assert result == mock_out
        # 状态机 release_from_programming 被调
        part.sm.release_from_programming.assert_called_once()

    async def test_release_with_only_g_code_succeeds(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        """有 G 代码但缺设定单也可 release（前置校验已移除）。"""
        from schema.part import PlaceOnShelfRequest
        from model.process import TProcess

        part = _make_part(status="PROGRAMMING")
        shelf = _make_shelf()
        process = TProcess(
            id=42, code="车", name="车床加工",
            category="INHOUSE", sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_processes.get_by_id.return_value = process
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [42]
        mock_out = _make_part_out()
        service._to_out.return_value = [mock_out]

        # G 代码有，设定单没有 —— 旧版（2026-07-10~2026-08-01）会因前置校验抛 BizError，
        # 现在 release 不再要求两个文件都在
        g_code_mock = MagicMock()
        g_code_mock.kind = "G_CODE"
        service.files = MagicMock()
        service.files.list_by_part = AsyncMock(
            side_effect=lambda pid, kind=None: [g_code_mock] if kind == "G_CODE" else []
        )

        result = await service.release_from_programming(
            part_id=1001,
            data=PlaceOnShelfRequest(shelf_id=shelf.id, next_process_id=process.id),
        )
        assert result == mock_out
        part.sm.release_from_programming.assert_called_once()

    async def test_with_both_files_succeeds(
        self,
        service: PartService,
        mock_parts: PartRepository,
        mock_shelves: ShelfRepository,
        mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        from schema.part import PlaceOnShelfRequest
        from model.process import TProcess

        part = _make_part(status="PROGRAMMING")
        shelf = _make_shelf()
        process = TProcess(
            id=42, code="车", name="车床加工",
            category="INHOUSE", sort_order=0,
        )
        process.created_at = datetime(2026, 1, 1)
        process.updated_at = datetime(2026, 1, 1)
        process.description = None
        process.deleted_at = None
        mock_parts.get_by_id.return_value = part
        mock_shelves.get_by_id.return_value = shelf
        mock_processes.get_by_id.return_value = process
        # 2026-07-17：下发要求货架已映射该工序
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [42]
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


# ============================================================
# 外协流程（2026-07-15 新增）
# ============================================================
class TestSendToOutsource:
    """PENDING / IN_PROCESS → OUTSOURCE：发送零件到外协公司。"""

    async def test_inprocess_to_outsource_happy(
        self, service, mock_parts, mock_outsource_companies, mock_outsource_company_process,
        mock_processes, mock_outsource_quotes, mock_quote_events, mock_shelf_process_repo,
        mock_outsource_shipments,
    ) -> None:
        # PR-H 2026-07-28：发送外协统一从「绑定了 OUTSOURCE 工序的货架」上发出。
        # PENDING 已不再支持，必须 IN_PROCESS + PRODUCTION_SHELF + holder 在 OUTSOURCE 集合中。
        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location="PRODUCTION_SHELF",
            current_holder_id=42,
            next_process_id=99,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)
        # OUTSOURCE-bound shelves：当前 holder 42 在集合内
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[42],
        )

        # 外协公司存在 + 启用
        company = MagicMock()
        company.id = 500
        company.name = "测试外协A"
        company.is_active = True
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)

        # 工序存在 + OUTSOURCE 类别
        from model.process import TProcess
        proc = TProcess(id=99, code="数控车", name="数控车", category="OUTSOURCE", sort_order=0, requires_approval=True)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        # 公司映射了该工序
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[99],
        )

        # 2026-07-30：按 (part_id, process_id) 查 APPROVED 报价
        approved_quote = MagicMock()
        approved_quote.id = 800
        approved_quote.price = Decimal("88.00")
        approved_quote.outsource_company_id = 500
        mock_outsource_quotes.get_approved_for_part_process = AsyncMock(return_value=approved_quote)

        mock_out = _make_part_out()
        service._to_out = AsyncMock(return_value=[mock_out])

        from schema.part import SendToOutsourceRequest
        result = await service.send_to_outsource(
            1001, SendToOutsourceRequest(
                outsource_company_id="500", next_process_id="99", version=0,
            ),
        )
        assert result == mock_out
        part.sm.send_to_outsource.assert_called_once()
        # 2026-07-30：报价不再改状态；创建 shipment
        mock_outsource_shipments.create.assert_awaited_once()

    async def test_company_not_found(
        self, service, mock_parts, mock_outsource_companies, mock_processes,
    ) -> None:
        part = _make_part()
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=None)

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc_info:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="999", next_process_id="99", version=0,
                ),
            )
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_NOT_FOUND

    async def test_company_inactive(
        self, service, mock_parts, mock_outsource_companies,
    ) -> None:
        part = _make_part()
        mock_parts.get_by_id = AsyncMock(return_value=part)
        company = MagicMock(id=1, name="X", is_active=False)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc_info:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="1", next_process_id="99", version=0,
                ),
            )
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_NOT_FOUND

    async def test_process_wrong_category(
        self, service, mock_parts, mock_outsource_companies, mock_processes,
    ) -> None:
        part = _make_part()
        mock_parts.get_by_id = AsyncMock(return_value=part)
        company = MagicMock(id=1, name="X", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)

        from model.process import TProcess
        proc = TProcess(id=99, code="车", name="车", category="INHOUSE", sort_order=0)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc_info:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="1", next_process_id="99", version=0,
                ),
            )
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS

    async def test_process_not_mapped_to_company(
        self, service, mock_parts, mock_outsource_companies, mock_outsource_company_process,
        mock_processes,
    ) -> None:
        part = _make_part()
        mock_parts.get_by_id = AsyncMock(return_value=part)
        company = MagicMock(id=1, name="X", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)

        from model.process import TProcess
        proc = TProcess(id=99, code="数控车", name="数控车", category="OUTSOURCE", sort_order=0, requires_approval=True)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        # 公司没映射该工序
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[100, 101],  # 不含 99
        )

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc_info:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="1", next_process_id="99", version=0,
                ),
            )
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_PROCESS_NOT_MAPPED


class TestReceiveFromOutsource:
    """OUTSOURCE → IN_PROCESS：外协回收，下发到生产货架。"""

    async def test_outsource_to_inprocess_happy(
        self, service, mock_parts, mock_shelves, mock_processes, mock_shelf_process_repo,
    ) -> None:
        part = _make_part(
            status=PartStatus.OUTSOURCE.value,
            location="OUTSOURCE_COMPANY",
            current_holder_id=500,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)

        shelf = _make_shelf(shelf_id=1, code="S1", zone=ShelfZone.PRODUCTION.value)
        mock_shelves.get_by_id = AsyncMock(return_value=shelf)
        mock_shelves.list_by_ids = AsyncMock(return_value=[shelf])

        from model.process import TProcess
        proc = TProcess(id=99, code="车", name="车", category="INHOUSE", sort_order=0)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)
        # 2026-07-17：外协回收要求货架已映射该工序
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [99]

        mock_out = _make_part_out()
        service._to_out = AsyncMock(return_value=[mock_out])

        result = await service.receive_from_outsource(
            1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=99),
        )
        assert result == mock_out
        part.sm.receive_from_outsource.assert_called_once()

    async def test_process_must_be_inhouse(
        self, service, mock_parts, mock_shelves, mock_processes, mock_shelf_process_repo,
    ) -> None:
        part = _make_part(
            status=PartStatus.OUTSOURCE.value, location="OUTSOURCE_COMPANY",
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)
        shelf = _make_shelf(shelf_id=1, zone=ShelfZone.PRODUCTION.value)
        mock_shelves.get_by_id = AsyncMock(return_value=shelf)

        from model.process import TProcess
        proc = TProcess(id=99, code="数控车", name="数控车", category="OUTSOURCE", sort_order=0, requires_approval=True)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)
        # 2026-07-17：让 OUTSOURCE 工序通过 shelf↔process 校验，让 INHOUSE
        # 类别校验（我们真正想测的）真正触发
        mock_shelf_process_repo.list_process_ids_by_shelf.return_value = [99]

        with pytest.raises(BizError) as exc_info:
            await service.receive_from_outsource(
                1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=99),
            )
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS


class TestReceiveFromOutsourceToInspection:
    """2026-07-16：OUTSOURCE → INSPECTION：外协回收直接送检。"""

    async def test_outsource_to_inspection_happy(
        self, service, mock_parts, mock_shelves,
    ) -> None:
        """外协件送品检货架（auto_pass_inspection=False）。"""
        from schema.part import ReceiveToInspectionRequest

        part = _make_part(
            status=PartStatus.OUTSOURCE.value, location="OUTSOURCE_COMPANY",
            current_holder_id=500,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)
        shelf = _make_shelf(shelf_id=2, code="INSP-1", zone=ShelfZone.INSPECTION.value)
        mock_shelves.get_by_id = AsyncMock(return_value=shelf)
        mock_out = _make_part_out()
        service._to_out = AsyncMock(return_value=[mock_out])

        result = await service.receive_from_outsource_to_inspection(
            1001, ReceiveToInspectionRequest(shelf_id="2", auto_pass_inspection=False),
        )
        assert result == mock_out
        part.sm.inspect_from_outsource.assert_called_once()

    async def test_outsource_to_inspection_production_shelf_rejected(
        self, service, mock_parts, mock_shelves,
    ) -> None:
        """PRODUCTION 货架被拒（zone 错）。"""
        from schema.part import ReceiveToInspectionRequest

        part = _make_part(status=PartStatus.OUTSOURCE.value, location="OUTSOURCE_COMPANY")
        mock_parts.get_by_id = AsyncMock(return_value=part)
        shelf = _make_shelf(shelf_id=3, code="P1", zone=ShelfZone.PRODUCTION.value)
        mock_shelves.get_by_id = AsyncMock(return_value=shelf)

        with pytest.raises(BizError) as exc:
            await service.receive_from_outsource_to_inspection(
                1001, ReceiveToInspectionRequest(shelf_id="3"),
            )
        assert exc.value.code == ErrCode.BIZ_SHELF_NO_MATCH_FOR_PROCESS

    async def test_outsource_to_inspection_shelf_not_found(
        self, service, mock_parts, mock_shelves,
    ) -> None:
        from schema.part import ReceiveToInspectionRequest
        part = _make_part(status=PartStatus.OUTSOURCE.value, location="OUTSOURCE_COMPANY")
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_shelves.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(BizError) as exc:
            await service.receive_from_outsource_to_inspection(
                1001, ReceiveToInspectionRequest(shelf_id="999"),
            )
        assert exc.value.code == ErrCode.BIZ_SHELF_NOT_FOUND


class TestSendToOutsourceDefenseGate:
    """2026-07-16：send_to_outsource 防御性扩展（状态资格 + 已批报价）。"""

    async def test_no_approved_quote(
        self, service, mock_parts, mock_outsource_companies,
        mock_outsource_company_process, mock_processes, mock_outsource_quotes,
        mock_shelf_process_repo,
    ) -> None:
        """无 APPROVED 报价 → BIZ_OUTSOURCE_QUOTE_NOT_APPROVED 400。

        PR-H 2026-07-28：默认 part 在 OUTSOURCE-bound 货架上（IN_PROCESS+PRODUCTION_SHELF+holder=42），
        通过新的闸门后到报价校验失败。
        """
        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location=PartLocation.PRODUCTION_SHELF.value,
            current_holder_id=42,
            next_process_id=99,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[42],
        )
        company = MagicMock(id=500, name="X", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        from model.process import TProcess
        proc = TProcess(id=99, code="数控车", name="数控车",
                        category="OUTSOURCE", sort_order=0, requires_approval=True)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[99],
        )
        # 没有 APPROVED 报价
        mock_outsource_quotes.get_approved_for_part_process = AsyncMock(return_value=None)

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="500", next_process_id="99", version=0,
                ),
            )
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_APPROVED

    async def test_status_not_eligible(
        self, service, mock_parts, mock_outsource_companies,
        mock_outsource_company_process, mock_processes, mock_outsource_quotes,
        mock_shelf_process_repo,
    ) -> None:
        """IN_PROCESS + 工人手中（location=WORKER） → 拒绝发送外协。

        PR-H 2026-07-28：现在拒绝原因是 BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF 422
        （统一闸门比 quote check 先到）。
        """
        # worker 持有 → location=WORKER → 不在 PRODUCTION_SHELF
        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location="WORKER",
            current_holder_id=42,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)
        # 即使 OUTSOURCE-bound 集合非空，WORKER 也不在闸门内
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[42],
        )
        company = MagicMock(id=500, name="X", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        from model.process import TProcess
        proc = TProcess(id=99, code="数控车", name="数控车",
                        category="OUTSOURCE", sort_order=0, requires_approval=True)
        proc.created_at = datetime(2025, 1, 1); proc.updated_at = datetime(2025, 1, 1)
        proc.description = None; proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[99],
        )

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="500", next_process_id="99", version=0,
                ),
            )
        # PR-H 2026-07-28：闸门升级 → BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF

    async def test_inprocess_outsource_process_eligible(
        self, service, mock_parts, mock_outsource_companies,
        mock_outsource_company_process, mock_processes, mock_outsource_quotes,
        mock_quote_events, mock_shelf_process_repo,
    ) -> None:
        """IN_PROCESS + PRODUCTION_SHELF + 在 OUTSOURCE-bound 货架 + 有报价 → 成功。

        PR-H 2026-07-28：除了原来状态资格外，还要求 part 位于绑了 OUTSOURCE 工序的货架。
        """
        # 准备 OUTSOURCE 工序对象用于读取 next_process.category
        from model.process import TProcess
        out_proc = TProcess(id=200, code="外工序",
                            category=ProcessCategory.OUTSOURCE.value,
                            sort_order=0, name="下道",
                            requires_approval=True)
        out_proc.created_at = datetime(2025, 1, 1)
        out_proc.updated_at = datetime(2025, 1, 1)
        out_proc.description = None
        out_proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=out_proc)

        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location=PartLocation.PRODUCTION_SHELF.value,
            current_holder_id=42,  # 在 OUTSOURCE-bound 货架上
            next_process_id=200,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[42],
        )
        company = MagicMock(id=500, name="X", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[200],
        )
        approved_quote = MagicMock()
        approved_quote.id = 800
        approved_quote.price = Decimal("88.00")
        approved_quote.outsource_company_id = 500
        mock_outsource_quotes.get_approved_for_part_process = AsyncMock(return_value=approved_quote)
        mock_out = _make_part_out()
        service._to_out = AsyncMock(return_value=[mock_out])

        from schema.part import SendToOutsourceRequest
        result = await service.send_to_outsource(
            1001, SendToOutsourceRequest(
                outsource_company_id="500", next_process_id="200", version=0,
            ),
        )
        assert result == mock_out
        part.sm.send_to_outsource.assert_called_once()
        # 2026-07-30：报价不再改状态


# ===================================================================
# 2026-07-28：send_to_outsource 乐观锁 + 直接发送分支（requires_approval=False）
# ===================================================================


class TestSendToOutsourceOCCAndDirect:
    """send_to_outsource 入口 OCC + 直接发送外协（C2 货架前置）。"""

    async def test_occ_mismatch_raises_409(
        self, service, mock_parts, mock_outsource_companies, mock_processes,
        mock_outsource_company_process, mock_shelf_process_repo,
    ) -> None:
        """data.version 与目标批次 batch.version 不一致 → BIZ_VERSION_CONFLICT 409。

        2026-07-30 批次化后 OCC 校验在批次上。
        """
        from model.process import TProcess
        proc = TProcess(
            id=99, code="测试外协", name="测试外协",
            category=ProcessCategory.OUTSOURCE.value,
            sort_order=0, requires_approval=True,
        )
        proc.created_at = datetime(2025, 1, 1)
        proc.updated_at = datetime(2025, 1, 1)
        proc.description = None
        proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        company = MagicMock(id=500, name="A 外协", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[99],
        )
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[1],
        )

        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location=PartLocation.PRODUCTION_SHELF.value,
            current_holder_id=1,
            next_process_id=99,
        )
        part.version = 5
        mock_parts.get_by_id = AsyncMock(return_value=part)
        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="500", next_process_id="99",
                    version=3,  # 不匹配（批次 version=0）
                ),
            )
        assert exc.value.code == ErrCode.BIZ_VERSION_CONFLICT
        assert exc.value.http_status == 409

    async def test_direct_send_on_c2_success(
        self, service, mock_parts, mock_shelves, mock_outsource_companies,
        mock_outsource_company_process, mock_processes, mock_outsource_quotes,
        mock_shelf_process_repo, mock_quote_events, mock_outsource_shipments,
    ) -> None:
        """process.requires_approval=False + part 在 OUTSOURCE-bound 货架 → 直接发送成功。

        PR-H 2026-07-28：闸门升级为「绑了 OUTSOURCE 工序的货架」（C2 是其中一个）。
        2026-07-30：报价回归纯审批对象，DIRECT 自动创建 is_direct=true APPROVED 报价，
        发送状态由 t_outsource_shipment 记录。
        """
        # 旧 C2 fixture 留作示意；新闸门走 list_shelf_ids_with_process_category
        c2 = MagicMock(spec=TShelf)
        c2.id = 99
        c2.code = "C2"
        c2.zone = ShelfZone.PRODUCTION.value
        c2.is_active = True
        c2.deleted_at = None
        mock_shelves.get_by_code = AsyncMock(return_value=c2)
        # PR-H 2026-07-28：C2 在 OUTSOURCE-bound 集合中
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[99],
        )

        from model.process import TProcess
        proc = TProcess(
            id=200, code="热处理", name="热处理",
            category=ProcessCategory.OUTSOURCE.value,
            sort_order=0, requires_approval=False,  # 直接发送开关
        )
        proc.created_at = datetime(2025, 1, 1)
        proc.updated_at = datetime(2025, 1, 1)
        proc.description = None
        proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location=PartLocation.PRODUCTION_SHELF.value,
            next_process_id=200,
            current_holder_id=99,  # 坐在 C2 上
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)

        company = MagicMock(id=500, name="A 外协", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[200],
        )

        # 即使有 quote repo mock，直接发送分支不应被调用
        mock_outsource_quotes.get_one_approved = AsyncMock(
            side_effect=AssertionError("直接发送不应查旧报价"),
        )

        mock_out = _make_part_out()
        service._to_out = AsyncMock(return_value=[mock_out])

        from schema.part import SendToOutsourceRequest
        result = await service.send_to_outsource(
            1001, SendToOutsourceRequest(
                outsource_company_id="500", next_process_id="200", version=0,
            ),
        )
        assert result == mock_out
        part.sm.send_to_outsource.assert_called_once()
        # 验证 direct_send=True 被传入（关键字参数）
        call_kwargs = part.sm.send_to_outsource.call_args.kwargs
        assert call_kwargs.get("direct_send") is True

        # 2026-07-30：DIRECT 自动创建 price=0 APPROVED 占位报价，报价本身不变状态
        from model.enums import OutsourceQuoteStatus as _OQS
        created_quote = mock_outsource_quotes.create.await_args.args[0]
        assert created_quote.price == Decimal("0")
        assert created_quote.review_note == "系统自动创建（DIRECT 直接发送）"
        assert created_quote.status == _OQS.APPROVED.value
        assert created_quote.is_direct is True
        # 报价不再写 sent_at/quantity；由 shipment 承载发货记录
        assert created_quote.sent_at is None
        assert created_quote.quantity is None
        # 不 update 报价（状态未变）
        mock_outsource_quotes.update.assert_not_awaited()
        # 报价事件：CREATED（DIRECT 自动）一条
        assert mock_quote_events.create.await_count == 1
        # shipment 记录创建
        assert mock_outsource_shipments.create.await_count == 1

    async def test_direct_send_part_not_on_c2_raises_422(
        self, service, mock_parts, mock_shelves, mock_outsource_companies,
        mock_outsource_company_process, mock_processes, mock_outsource_quotes,
        mock_shelf_process_repo,
    ) -> None:
        """process.requires_approval=False 但 part 不在 OUTSOURCE-bound 货架 → 422。

        PR-H 2026-07-28：闸门从 C2 特例升级为 OUTSOURCE-bound 集合。
        """
        c2 = MagicMock(spec=TShelf)
        c2.id = 99
        c2.code = "C2"
        c2.zone = ShelfZone.PRODUCTION.value
        c2.is_active = True
        c2.deleted_at = None
        mock_shelves.get_by_code = AsyncMock(return_value=c2)
        # OUTSOURCE-bound 集合只含 99 (C2)；part 坐在 11 (A1) 上
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[99],
        )

        from model.process import TProcess
        proc = TProcess(
            id=200, code="热处理", name="热处理",
            category=ProcessCategory.OUTSOURCE.value,
            sort_order=0, requires_approval=False,
        )
        proc.created_at = datetime(2025, 1, 1)
        proc.updated_at = datetime(2025, 1, 1)
        proc.description = None
        proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        # part 在 A1 货架（holder != C2.id）
        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location=PartLocation.PRODUCTION_SHELF.value,
            next_process_id=200,
            current_holder_id=11,  # 别的货架
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)

        company = MagicMock(id=500, name="A 外协", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[200],
        )

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="500", next_process_id="200", version=0,
                ),
            )
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF
        assert exc.value.http_status == 422
        part.sm.send_to_outsource.assert_not_called()

    async def test_direct_send_c2_missing_raises_500(
        self, service, mock_parts, mock_shelves, mock_outsource_companies,
        mock_outsource_company_process, mock_processes, mock_shelf_process_repo,
    ) -> None:
        """系统无任何 OUTSOURCE-bound 货架 → BIZ_OUTSOURCE_NO_SHELF 500（运维配置错误）。

        PR-H 2026-07-28：从「C2 不存在」改为「OUTSOURCE-bound 货架集合为空」。
        """
        mock_shelves.get_by_code = AsyncMock(return_value=None)
        mock_shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[],  # 无任何 OUTSOURCE-bound 货架
        )

        from model.process import TProcess
        proc = TProcess(
            id=200, code="热处理", name="热处理",
            category=ProcessCategory.OUTSOURCE.value,
            sort_order=0, requires_approval=False,
        )
        proc.created_at = datetime(2025, 1, 1)
        proc.updated_at = datetime(2025, 1, 1)
        proc.description = None
        proc.deleted_at = None
        mock_processes.get_by_id = AsyncMock(return_value=proc)

        part = _make_part(
            status=PartStatus.IN_PROCESS.value,
            location=PartLocation.PRODUCTION_SHELF.value,
            next_process_id=200,
            current_holder_id=99,
        )
        mock_parts.get_by_id = AsyncMock(return_value=part)

        company = MagicMock(id=500, name="A 外协", is_active=True)
        mock_outsource_companies.get_by_id = AsyncMock(return_value=company)
        mock_outsource_company_process.list_process_ids_by_outsource_company = AsyncMock(
            return_value=[200],
        )

        from schema.part import SendToOutsourceRequest
        with pytest.raises(BizError) as exc:
            await service.send_to_outsource(
                1001, SendToOutsourceRequest(
                    outsource_company_id="500", next_process_id="200", version=0,
                ),
            )
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_NO_SHELF
        assert exc.value.http_status == 500


# ===================================================================
# 2026-07-17：shelf ↔ process 映射守卫
# ===================================================================


class TestShelfProcessGuard:
    """2026-07-17 新增 invariant：所有走 `(shelf, next_process)` 的写路径都必须
    校验 `shelf_process_repo.list_process_ids_by_shelf(shelf.id)` 包含目标 process。

    4 个入口：
    - `place_on_shelf`            (CLERK / MANAGER 下发零件)
    - `release_from_programming`  (CNC 编程员下发)
    - `receive_from_outsource`    (外协回收)
    - `scan_event RETURNED`       (工人放回)
    - `complete_repair`           (返修完成——校验 carried next_process_id)

    失败 → `BIZ_SHELF_PROCESS_NOT_MAPPED` 422 中文文案。
    """

    async def _make_process(self, pid: int = 42, category: str = "INHOUSE"):
        from model.process import TProcess
        p = TProcess(
            id=pid, code=f"P{pid}", name=f"P{pid}",
            category=category, sort_order=0,
        )
        p.created_at = datetime(2026, 1, 1)
        p.updated_at = datetime(2026, 1, 1)
        p.description = None
        p.deleted_at = None
        return p

    # -- place_on_shelf ------------------------------------------------

    async def test_place_on_shelf_rejects_unmapped(
        self, service, mock_parts, mock_shelves, mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        from schema.part import PlaceOnShelfRequest
        mock_parts.get_by_id = AsyncMock(return_value=_make_part())
        mock_shelves.get_by_id = AsyncMock(return_value=_make_shelf())
        mock_processes.get_by_id = AsyncMock(return_value=await self._make_process(42))
        mock_shelf_process_repo.list_process_ids_by_shelf = AsyncMock(return_value=[])

        with pytest.raises(BizError) as exc:
            await service.place_on_shelf(
                1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42),
            )
        assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED
        assert exc.value.http_status == http_status.HTTP_422_UNPROCESSABLE_ENTITY

    # -- release_from_programming --------------------------------------

    async def test_release_from_programming_rejects_unmapped(
        self, service, mock_parts, mock_shelves, mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        from schema.part import PlaceOnShelfRequest
        mock_parts.get_by_id = AsyncMock(return_value=_make_part(status="PROGRAMMING"))
        mock_shelves.get_by_id = AsyncMock(return_value=_make_shelf())
        mock_processes.get_by_id = AsyncMock(return_value=await self._make_process(42))
        mock_shelf_process_repo.list_process_ids_by_shelf = AsyncMock(return_value=[])
        # 文件齐备
        g_mock = MagicMock(); g_mock.kind = "G_CODE"
        s_mock = MagicMock(); s_mock.kind = "SETUP_SHEET"
        service.files = MagicMock()
        service.files.list_by_part = AsyncMock(side_effect=lambda pid, kind=None:
            {"G_CODE": [g_mock], "SETUP_SHEET": [s_mock]}.get(kind, []))

        with pytest.raises(BizError) as exc:
            await service.release_from_programming(
                1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42),
            )
        assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED

    # -- receive_from_outsource ----------------------------------------

    async def test_receive_from_outsource_rejects_unmapped(
        self, service, mock_parts, mock_shelves, mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        from schema.part import PlaceOnShelfRequest
        mock_parts.get_by_id = AsyncMock(return_value=_make_part(
            status=PartStatus.OUTSOURCE.value, location="OUTSOURCE_COMPANY",
        ))
        mock_shelves.get_by_id = AsyncMock(return_value=_make_shelf())
        mock_processes.get_by_id = AsyncMock(return_value=await self._make_process(99))
        mock_shelf_process_repo.list_process_ids_by_shelf = AsyncMock(return_value=[])

        with pytest.raises(BizError) as exc:
            await service.receive_from_outsource(
                1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=99),
            )
        assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED

    # -- scan_event RETURNED 严格模式 ------------------------------------

    async def test_scan_event_returned_strict_no_permissive_skip(
        self, service, mock_parts, mock_shelves, mock_workers,
        mock_processes, mock_shelf_process_repo,
    ) -> None:
        """empty mapping 现在也拒绝（之前是 permissive `if allowed_ids` 跳过）。"""
        from schema.part import PartScanRequest
        from model.enums import PartEventType
        part = _make_part(status="IN_PROCESS", location="WORKER", current_holder_id=1)
        shelf = _make_shelf()
        worker = _make_worker()
        mock_shelves.get_by_id = AsyncMock(return_value=shelf)
        mock_workers.get_by_badge_code = AsyncMock(return_value=worker)
        mock_parts.get_by_serial = AsyncMock(return_value=part)
        mock_processes.get_by_id = AsyncMock(return_value=await self._make_process(99))
        mock_shelf_process_repo.list_process_ids_by_shelf = AsyncMock(return_value=[])

        data = PartScanRequest(
            serial_no="L0001",
            event_type=PartEventType.RETURNED,
            shelf_id=1,
            badge_code="W001",
            next_process_id=99,
        )
        with pytest.raises(BizError) as exc:
            await service.scan_event(data)
        assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED
        assert exc.value.http_status == http_status.HTTP_422_UNPROCESSABLE_ENTITY

    # -- complete_repair 守卫 carried next_process_id ------------------

    async def test_complete_repair_rejects_when_carried_process_unmapped(
        self, service, mock_parts, mock_shelves, mock_processes,
        mock_shelf_process_repo,
    ) -> None:
        """complete_repair：part.next_process_id 非空时校验 shelf 是否映射。"""
        part = _make_part(status="REPAIRING")
        part.next_process_id = 99
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_shelves.get_by_id = AsyncMock(return_value=_make_shelf())
        mock_processes.get_by_id = AsyncMock(return_value=await self._make_process(99))
        mock_shelf_process_repo.list_process_ids_by_shelf = AsyncMock(return_value=[])

        with pytest.raises(BizError) as exc:
            await service.complete_repair(part_id=1001, shelf_id=1)
        assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED

    async def test_complete_repair_skips_guard_when_next_process_id_none(
        self, service, mock_parts, mock_shelves,
    ) -> None:
        """next_process_id IS NULL（fail_inspection 清空）时跳过守卫。"""
        part = _make_part(status="REPAIRING")
        part.next_process_id = None
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_shelves.get_by_id = AsyncMock(return_value=_make_shelf())
        mock_out = _make_part_out()
        service._to_out = AsyncMock(return_value=[mock_out])

        result = await service.complete_repair(part_id=1001, shelf_id=1)
        assert result == mock_out
        part.sm.complete_repair.assert_called_once()

    # -- 防御：shelf_process_repo is None → 500 ------------------------

    async def test_repo_none_raises_500(
        self, mock_parts, mock_customers, mock_workers, mock_events,
        mock_serial_counters, mock_shelves, mock_processes,
        mock_work_types, mock_work_type_process,
    ) -> None:
        """构造 PartService 时不传 shelf_process_repo → 守卫应抛 500。"""
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
            # 故意不传 shelf_process_repo
            shelf_process_repo=None,
        )
        from schema.part import PlaceOnShelfRequest
        mock_parts.get_by_id = AsyncMock(return_value=_make_part())
        mock_shelves.get_by_id = AsyncMock(return_value=_make_shelf())
        mock_processes.get_by_id = AsyncMock(return_value=await self._make_process(42))

        with pytest.raises(BizError) as exc:
            await svc.place_on_shelf(
                1001, PlaceOnShelfRequest(shelf_id=1, next_process_id=42),
            )
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_500_INTERNAL_SERVER_ERROR
