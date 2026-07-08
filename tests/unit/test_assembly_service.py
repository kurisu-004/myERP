"""Unit tests for AssemblyService (service/assembly.py).

Tests all public methods and the standalone _parse_status function.
Repository and service dependencies are mocked with AsyncMock / MagicMock.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TAssembly, TCustomer, TPart
from repository import (
    AssemblyRepository,
    CustomerRepository,
    DrawingFileRepository,
    PartEventRepository,
    PartRepository,
    SerialCounterRepository,
)
from schema.assembly import (
    AssemblyChildCreateRequest,
    AssemblyCreateRequest,
    AssemblyListOut,
    AssemblyListQuery,
    AssemblyOut,
)
from schema.drawing import DrawingFileOut
from schema.part import PartOut
from service.assembly import AssemblyService, _parse_status
from service.drawing import DrawingService
from service.part import PartService

pytestmark = pytest.mark.asyncio


# ============================================================
# Helper factories
# ============================================================


def make_assembly(**kwargs):
    """Create a MagicMock TAssembly with standard defaults."""
    defaults = dict(
        id=1001,
        serial_no=None,
        drawing_no="DWG-001",
        name="Test Assembly",
        applicant_name="Applicant",
        customer_id=1,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        actual_delivery_date=None,
        is_urgent=False,
        status="PENDING",
        created_at=datetime(2026, 7, 1, 10, 0, 0),
        updated_at=datetime(2026, 7, 1, 10, 0, 0),
    )
    params = {**defaults, **kwargs}
    asm = MagicMock(spec=TAssembly)
    for k, v in params.items():
        setattr(asm, k, v)
    asm.sm = MagicMock()
    return asm


def make_part(**kwargs):
    """Create a MagicMock TPart with standard defaults."""
    defaults = dict(
        id=2001,
        serial_no="L0001",
        name="Child Part",
        drawing_no="PART-001",
        applicant_name="Applicant",
        quantity=1,
        unit_price=Decimal("10.00"),
        total_price=Decimal("10.00"),
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        actual_delivery_date=None,
        status="PENDING",
        is_urgent=False,
        customer_id=1,
        assembly_id=1001,
        location="OFFICE",
        current_holder_id=None,
        placed_at=None,
        created_at=datetime(2026, 7, 1, 10, 0, 0),
        updated_at=datetime(2026, 7, 1, 10, 0, 0),
    )
    params = {**defaults, **kwargs}
    part = MagicMock(spec=TPart)
    for k, v in params.items():
        setattr(part, k, v)
    part.sm = MagicMock()
    return part


def make_customer(**kwargs):
    """Create a MagicMock TCustomer with standard defaults."""
    defaults = dict(id=1, name="Test Customer", parent_id=None)
    params = {**defaults, **kwargs}
    cust = MagicMock(spec=TCustomer)
    for k, v in params.items():
        setattr(cust, k, v)
    return cust


def make_drawing_file(**kwargs):
    """Create a MagicMock TDrawingFile with standard defaults."""
    defaults = dict(id=3001, object_key="drawings/assembly/1001/3001.pdf")
    params = {**defaults, **kwargs}
    f = MagicMock()
    for k, v in params.items():
        setattr(f, k, v)
    return f


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_assemblies():
    m = MagicMock(spec=AssemblyRepository)
    m.session = AsyncMock()
    m.session.flush = AsyncMock()
    m.list_with_filters = AsyncMock(return_value=[])
    m.count_with_filters = AsyncMock(return_value=0)
    m.get_by_id = AsyncMock(return_value=None)
    m.create = AsyncMock()
    m.soft_delete = AsyncMock()
    return m


@pytest.fixture
def mock_parts():
    m = MagicMock(spec=PartRepository)
    m.session = AsyncMock()
    m.session.flush = AsyncMock()
    m.session.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
    m.list_with_filters = AsyncMock(return_value=[])
    m.list_children = AsyncMock(return_value=[])
    m.get_by_id = AsyncMock(return_value=None)
    m.create = AsyncMock()
    m.soft_delete = AsyncMock()
    m.list_by_ids = AsyncMock(return_value=[])
    return m


@pytest.fixture
def mock_files():
    m = MagicMock(spec=DrawingFileRepository)
    m.list_for_part_ids = AsyncMock(return_value=[])
    m.list_by_assembly = AsyncMock(return_value=[])
    m.soft_delete_many = AsyncMock()
    m.create = AsyncMock()
    return m


@pytest.fixture
def mock_customers():
    m = MagicMock(spec=CustomerRepository)
    m.get_by_id = AsyncMock(return_value=None)
    m.list_by_ids = AsyncMock(side_effect=lambda ids: [])
    return m


@pytest.fixture
def mock_serial_counters():
    m = MagicMock(spec=SerialCounterRepository)
    m.acquire_serial = AsyncMock(return_value="L0001")
    return m


@pytest.fixture
def mock_events():
    m = MagicMock(spec=PartEventRepository)
    m.create = AsyncMock()
    m.list_by_part = AsyncMock(return_value=[])
    return m


@pytest.fixture
def mock_part_service():
    m = MagicMock(spec=PartService)
    m._to_out = AsyncMock(return_value=[])
    return m


@pytest.fixture
def mock_drawings():
    m = MagicMock(spec=DrawingService)
    m.list_for_assembly = AsyncMock(return_value=[])
    m.delete_files_silently = AsyncMock()
    m._to_out = AsyncMock()
    return m


@pytest.fixture
def svc(mock_assemblies, mock_parts, mock_files, mock_customers,
        mock_serial_counters, mock_events, mock_part_service, mock_drawings):
    return AssemblyService(
        assemblies=mock_assemblies,
        parts=mock_parts,
        files=mock_files,
        customers=mock_customers,
        serial_counters=mock_serial_counters,
        events=mock_events,
        part_service=mock_part_service,
        drawings=mock_drawings,
    )


# ============================================================
# list_assemblies
# ============================================================


class TestListAssemblies:
    """list_assemblies(query: AssemblyListQuery) -> AssemblyListOut."""

    async def test_normal_query(self, svc):
        """Normal query with filters -> AssemblyListOut with items."""
        # Arrange
        asm1 = make_assembly(id=1001, customer_id=1)
        asm2 = make_assembly(id=1002, customer_id=2, status="IN_PROCESS")

        svc.assemblies.list_with_filters = AsyncMock(return_value=[asm1, asm2])
        svc.assemblies.count_with_filters = AsyncMock(return_value=2)

        # Mock _count_children_for: parts.session.execute
        mock_result = MagicMock()
        mock_result.all.return_value = [(1001, 3), (1002, 5)]
        svc.parts.session.execute = AsyncMock(return_value=mock_result)

        # Mock _load_cust_map: customers.list_by_ids
        cust1 = make_customer(id=1, name="Child 1", parent_id=10)
        cust2 = make_customer(id=2, name="Child 2", parent_id=10)
        parent = make_customer(id=10, name="Parent Corp", parent_id=None)

        _all_custs = {1: cust1, 2: cust2, 10: parent}

        async def mock_list_by_ids(ids):
            return [_all_custs[i] for i in ids if i in _all_custs]

        svc.customers.list_by_ids = mock_list_by_ids

        q = AssemblyListQuery(limit=50, offset=0)

        # Act
        result = await svc.list_assemblies(q)

        # Assert
        assert isinstance(result, AssemblyListOut)
        assert len(result.items) == 2
        assert result.total == 2
        assert result.limit == 50

        # Child counts passed through
        assert result.items[0].child_count == 3
        assert result.items[1].child_count == 5

        # Customer path
        assert result.items[0].customer_name == "Child 1"
        assert result.items[0].parent_customer_name == "Parent Corp"
        assert result.items[0].customer_path == "Parent Corp / Child 1"

        svc.assemblies.list_with_filters.assert_awaited_once()
        svc.assemblies.count_with_filters.assert_awaited_once()

    async def test_empty_result(self, svc):
        """Empty result -> items=[] total=0."""
        svc.assemblies.list_with_filters = AsyncMock(return_value=[])
        svc.assemblies.count_with_filters = AsyncMock(return_value=0)

        q = AssemblyListQuery(limit=100, offset=0)
        result = await svc.list_assemblies(q)

        assert result.items == []
        assert result.total == 0
        assert result.limit == 100

    async def test_status_filter_parsing(self, svc):
        """_parse_status handles None, valid, and invalid inside list_assemblies."""
        svc.assemblies.list_with_filters = AsyncMock(return_value=[])
        svc.assemblies.count_with_filters = AsyncMock(return_value=0)

        # None -> passes None through
        await svc.list_assemblies(AssemblyListQuery(status=None))
        assert svc.assemblies.list_with_filters.await_args.kwargs["status"] is None

        svc.assemblies.list_with_filters.reset_mock()
        svc.assemblies.count_with_filters.reset_mock()

        # Valid (case-insensitive, stripped) -> normalized
        await svc.list_assemblies(AssemblyListQuery(status="  in_process  "))
        assert svc.assemblies.list_with_filters.await_args.kwargs["status"] == "IN_PROCESS"

        svc.assemblies.list_with_filters.reset_mock()
        svc.assemblies.count_with_filters.reset_mock()

        # Invalid -> BizError
        with pytest.raises(BizError) as exc:
            await svc.list_assemblies(AssemblyListQuery(status="INVALID_STATUS"))
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400


# ============================================================
# get_assembly_detail
# ============================================================


class TestGetAssemblyDetail:
    """get_assembly_detail(assembly_id: int) -> AssemblyDetail."""

    async def test_assembly_exists(self, svc):
        """Assembly exists -> _build_detail called -> AssemblyDetail returned."""
        asm = make_assembly()
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)

        svc.drawings.list_for_assembly = AsyncMock(return_value=[])
        svc.parts.list_children = AsyncMock(return_value=[make_part()])

        mock_part_out = MagicMock(spec=PartOut)
        svc.part_service._to_out = AsyncMock(return_value=[mock_part_out])

        # Mock customers for _load_cust_map
        cust = make_customer(id=1, name="Child", parent_id=10)
        parent = make_customer(id=10, name="Parent", parent_id=None)
        _custs = {1: cust, 10: parent}

        async def mock_list_by_ids(ids):
            return [_custs[i] for i in ids if i in _custs]

        svc.customers.list_by_ids = mock_list_by_ids

        result = await svc.get_assembly_detail(1001)

        assert result.assembly.id == 1001
        assert result.assembly.drawing_no == "DWG-001"
        assert len(result.children) == 1
        assert len(result.files) == 0
        svc.assemblies.get_by_id.assert_awaited_with(1001)

    async def test_assembly_not_found(self, svc):
        """Assembly not found -> BizError BIZ_ASSEMBLY_NOT_FOUND 404."""
        svc.assemblies.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.get_assembly_detail(9999)
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert exc.value.http_status == 404


# ============================================================
# get_assembly_for_child
# ============================================================


class TestGetAssemblyForChild:
    """get_assembly_for_child(child_part_id: int) -> AssemblyDetail."""

    async def test_normal(self, svc):
        """Part found with assembly_id -> returns parent AssemblyDetail."""
        part = make_part(assembly_id=1001)
        asm = make_assembly()

        svc.parts.get_by_id = AsyncMock(return_value=part)
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)

        svc.drawings.list_for_assembly = AsyncMock(return_value=[])
        svc.parts.list_children = AsyncMock(return_value=[part])
        mock_part_out = MagicMock(spec=PartOut)
        svc.part_service._to_out = AsyncMock(return_value=[mock_part_out])

        cust = make_customer(id=1, name="Child", parent_id=10)
        parent = make_customer(id=10, name="Parent", parent_id=None)

        async def mock_list_by_ids(ids):
            mapping = {1: cust, 10: parent}
            return [mapping[i] for i in ids if i in mapping]

        svc.customers.list_by_ids = mock_list_by_ids

        result = await svc.get_assembly_for_child(2001)

        assert isinstance(result.assembly, AssemblyOut)
        assert result.assembly.id == 1001
        assert len(result.children) == 1

    async def test_part_not_found(self, svc):
        """Part not found -> BizError BIZ_PART_NOT_FOUND 404."""
        svc.parts.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.get_assembly_for_child(9999)
        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_part_no_assembly_id(self, svc):
        """Part has no assembly_id -> BizError BIZ_ASSEMBLY_NOT_FOUND 404."""
        part = make_part(assembly_id=None)
        svc.parts.get_by_id = AsyncMock(return_value=part)

        with pytest.raises(BizError) as exc:
            await svc.get_assembly_for_child(2001)
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert exc.value.http_status == 404
        assert "不属于任何装配件" in str(exc.value.message)

    async def test_parent_assembly_not_found(self, svc):
        """Assembly (parent) not found -> BizError BIZ_ASSEMBLY_NOT_FOUND 404."""
        part = make_part(assembly_id=1001)
        svc.parts.get_by_id = AsyncMock(return_value=part)
        svc.assemblies.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.get_assembly_for_child(2001)
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert exc.value.http_status == 404


# ============================================================
# cancel_assembly
# ============================================================


class TestCancelAssembly:
    """cancel_assembly(assembly_id: int) -> AssemblyDetail."""

    async def test_normal(self, svc):
        """Cancels non-terminal children + assembly itself."""
        asm = make_assembly(status="IN_PROCESS")
        child_active = make_part(id=2001, status="IN_PROCESS")
        child_pending = make_part(id=2002, status="PENDING")

        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[child_active, child_pending])

        svc.drawings.list_for_assembly = AsyncMock(return_value=[])
        mock_part_out = MagicMock(spec=PartOut)
        svc.part_service._to_out = AsyncMock(return_value=[mock_part_out, mock_part_out])

        cust = make_customer(id=1, name="Child", parent_id=10)
        parent = make_customer(id=10, name="Parent", parent_id=None)

        async def mock_list_by_ids(ids):
            mapping = {1: cust, 10: parent}
            return [mapping[i] for i in ids if i in mapping]

        svc.customers.list_by_ids = mock_list_by_ids

        # Act
        result = await svc.cancel_assembly(1001)

        # Assert both children got cancelled
        child_active.sm.cancel.assert_called_once()
        child_pending.sm.cancel.assert_called_once()
        asm.sm.cancel.assert_called_once()
        assert result.assembly.status == "IN_PROCESS"

        svc.parts.session.flush.assert_called()
        svc.assemblies.session.flush.assert_called_once()

    async def test_not_found(self, svc):
        """Assembly not found -> BizError BIZ_ASSEMBLY_NOT_FOUND 404."""
        svc.assemblies.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.cancel_assembly(9999)
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_children_terminal(self, svc):
        """Children in terminal state -> not cancelled again."""
        asm = make_assembly(status="PENDING")
        child_done = make_part(id=2001, status="COMPLETED")
        child_cancelled = make_part(id=2002, status="CANCELLED")

        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[child_done, child_cancelled])

        svc.drawings.list_for_assembly = AsyncMock(return_value=[])
        mock_part_out = MagicMock(spec=PartOut)
        svc.part_service._to_out = AsyncMock(return_value=[mock_part_out, mock_part_out])

        cust = make_customer(id=1, name="Child", parent_id=10)
        parent = make_customer(id=10, name="Parent", parent_id=None)

        async def mock_list_by_ids(ids):
            mapping = {1: cust, 10: parent}
            return [mapping[i] for i in ids if i in mapping]

        svc.customers.list_by_ids = mock_list_by_ids

        await svc.cancel_assembly(1001)

        # Terminal children NOT cancelled
        child_done.sm.cancel.assert_not_called()
        child_cancelled.sm.cancel.assert_not_called()

        # Assembly itself IS cancelled
        asm.sm.cancel.assert_called_once()


# ============================================================
# soft_delete_assembly
# ============================================================


class TestSoftDeleteAssembly:
    """soft_delete_assembly(assembly_id: int) -> None."""

    async def test_normal(self, svc):
        """Soft deletes files, parts, assembly."""
        asm = make_assembly()
        child = make_part()
        child_file = make_drawing_file(id=3001, object_key="key-child.pdf")
        asm_file = make_drawing_file(id=3002, object_key="key-asm.pdf")

        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[child])
        svc.files.list_for_part_ids = AsyncMock(return_value=[child_file])
        svc.files.list_by_assembly = AsyncMock(return_value=[asm_file])

        # Act
        await svc.soft_delete_assembly(1001)

        # Assert
        svc.files.soft_delete_many.assert_awaited_once()
        files_arg = svc.files.soft_delete_many.await_args[0][0]
        assert len(files_arg) == 2  # child + asm files

        svc.parts.soft_delete.assert_awaited_once_with(child)
        svc.assemblies.soft_delete.assert_awaited_once_with(asm)
        svc.drawings.delete_files_silently.assert_awaited_once_with(
            ["key-child.pdf", "key-asm.pdf"]
        )

    async def test_not_found(self, svc):
        """Assembly not found -> BizError BIZ_ASSEMBLY_NOT_FOUND 404."""
        svc.assemblies.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.soft_delete_assembly(9999)
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert exc.value.http_status == 404

        svc.parts.soft_delete.assert_not_called()
        svc.assemblies.soft_delete.assert_not_called()

    async def test_no_children(self, svc):
        """No children -> only deletes assembly-level files, no parts."""
        asm = make_assembly()
        asm_file = make_drawing_file(id=3002, object_key="key-asm.pdf")

        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[])
        svc.files.list_for_part_ids = AsyncMock(return_value=[])
        svc.files.list_by_assembly = AsyncMock(return_value=[asm_file])

        await svc.soft_delete_assembly(1001)

        svc.files.soft_delete_many.assert_awaited_once()
        files_arg = svc.files.soft_delete_many.await_args[0][0]
        assert len(files_arg) == 1  # only asm file

        svc.parts.soft_delete.assert_not_called()
        svc.assemblies.soft_delete.assert_awaited_once_with(asm)
        svc.drawings.delete_files_silently.assert_awaited_once()


# ============================================================
# create_assembly — ERROR PATHS ONLY
# ============================================================


class TestCreateAssemblyErrors:
    """create_assembly(data, pdf_bytes, pdf_filename) — error paths."""

    @pytest.fixture
    def create_data(self):
        return AssemblyCreateRequest(
            name="Test Asm",
            drawing_no="DWG-001",
            customer_id="1",
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            children=[
                AssemblyChildCreateRequest(
                    drawing_no="PART-001",
                    name="Child Part",
                    quantity=1,
                    unit_price=Decimal("10.00"),
                    page_index=2,
                )
            ],
        )

    async def test_empty_pdf_bytes(self, svc, create_data):
        """Empty pdf_bytes -> BizError BIZ_DRAWING_FILE_TOO_LARGE 400."""
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"", pdf_filename="drawing.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_DRAWING_FILE_TOO_LARGE
        assert exc.value.http_status == 400

    async def test_non_pdf_filename(self, svc, create_data):
        """Non-PDF filename -> BizError BIZ_DRAWING_FILE_BAD_TYPE 400."""
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"fake content", pdf_filename="drawing.jpg"
            )
        assert exc.value.code == ErrCode.BIZ_DRAWING_FILE_BAD_TYPE
        assert exc.value.http_status == 400

    async def test_customer_not_found(self, svc, create_data):
        """Customer not found -> BizError BIZ_ASSEMBLY_BAD_CUSTOMER 404."""
        svc.customers.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"fake content", pdf_filename="drawing.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER
        assert exc.value.http_status == 404


# ============================================================
# _parse_status (standalone function)
# ============================================================


class TestParseStatus:
    """_parse_status(value: str | None) -> str | None."""

    async def test_none(self):
        assert _parse_status(None) is None

    async def test_valid(self):
        # Exact match
        assert _parse_status("PENDING") == "PENDING"
        assert _parse_status("IN_PROCESS") == "IN_PROCESS"
        assert _parse_status("COMPLETED") == "COMPLETED"
        assert _parse_status("CANCELLED") == "CANCELLED"
        # Case-insensitive
        assert _parse_status("in_process") == "IN_PROCESS"
        assert _parse_status("In_Process") == "IN_PROCESS"
        # Stripped
        assert _parse_status("  completed  ") == "COMPLETED"

    async def test_invalid(self):
        with pytest.raises(BizError) as exc:
            _parse_status("INVALID_STATUS")
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400


# ============================================================
# 装配体流水号（serial_no）分配 / 派生 / 释放
# ============================================================


class TestAssemblySerialAllocation:
    """create_assembly uses ONE acquire_serial call → derives children as f"{serial}-{i:02d}"."""

    @pytest.fixture
    def happy_path_create_data(self):
        return AssemblyCreateRequest(
            name="Test Asm",
            drawing_no="DWG-ASM-001",
            customer_id="1",
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            children=[
                AssemblyChildCreateRequest(
                    drawing_no="PART-A",
                    name="Child A",
                    quantity=1,
                    unit_price=Decimal("10.00"),
                    page_index=2,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="PART-B",
                    name="Child B",
                    quantity=1,
                    unit_price=Decimal("10.00"),
                    page_index=3,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="PART-C",
                    name="Child C",
                    quantity=1,
                    unit_price=Decimal("10.00"),
                    page_index=4,
                ),
            ],
        )

    async def test_one_acquire_serial_per_assembly(
        self,
        svc,
        mock_customers,
        mock_serial_counters,
        mock_parts,
        mock_drawings,
        happy_path_create_data,
    ):
        """acquire_serial called exactly ONCE; children get derived serials."""
        from schema.drawing import DrawingFileOut

        with patch("service.assembly.cos_mod.upload_object", new=AsyncMock()):
            # arrange
            leaf = make_customer(id=1, name="Luda Sub", parent_id=10)
            parent = make_customer(id=10, name="路达", parent_id=None)

            async def mock_get_by_id(cid):
                if cid == 1: return leaf
                if cid == 10: return parent
                return None
            mock_customers.get_by_id.side_effect = mock_get_by_id

            mock_serial_counters.acquire_serial.return_value = "L1067"

            captured_children: list[TPart] = []

            async def mock_create_part(p):
                captured_children.append(p)
                return p
            mock_parts.create.side_effect = mock_create_part

            svc.parts.list_children = AsyncMock(return_value=[])
            # Stub heavy conversion with stub schema instance
            stub_file = DrawingFileOut(
                id="1",
                owner_type="assembly",
                owner_id="1",
                file_type="PDF",
                original_filename="test.pdf",
                file_size=10,
                content_type="application/pdf",
                page_index=None,
                download_url="https://example.com/x",
                upload_status="READY",
                created_at=datetime(2026, 7, 1, 10, 0, 0),
            )
            mock_drawings._to_out = AsyncMock(return_value=stub_file)
            svc._assembly_to_out = AsyncMock(
                return_value=MagicMock(spec=AssemblyOut)
            )
            svc.part_service._to_out = AsyncMock(return_value=[])

            # act
            await svc.create_assembly(
                happy_path_create_data,
                pdf_bytes=b"%PDF-1.4 fake",
                pdf_filename="test.pdf",
            )

            # assert: serial allocated exactly once, value bound to assembly
            mock_serial_counters.acquire_serial.assert_awaited_once()
            assert len(captured_children) == 3
            assert captured_children[0].serial_no == "L1067-01"
            assert captured_children[1].serial_no == "L1067-02"
            assert captured_children[2].serial_no == "L1067-03"

    async def test_too_many_children_raises(
        self,
        svc,
        mock_customers,
        mock_serial_counters,
        mock_parts,
    ):
        """100+ children → BizError BIZ_ASSEMBLY_TOO_MANY_CHILDREN (no counter touched)."""
        with patch("service.assembly.cos_mod.upload_object", new=AsyncMock()):
            # arrange: 100 children
            children = [
                AssemblyChildCreateRequest(
                    drawing_no=f"P{i}", name=f"C{i}", quantity=1,
                    unit_price=Decimal("1"), page_index=i + 1,
                )
                for i in range(1, 101)
            ]
            data = AssemblyCreateRequest(
                name="TooBig",
                drawing_no="DWG-TOO-BIG",
                customer_id="1",
                request_date=date(2026, 7, 1),
                planned_delivery_date=date(2026, 8, 1),
                children=children,
            )
            leaf = make_customer(id=1, name="Luda Sub", parent_id=10)
            parent = make_customer(id=10, name="路达", parent_id=None)
            async def mock_get_by_id(cid):
                if cid == 1: return leaf
                if cid == 10: return parent
                return None
            mock_customers.get_by_id.side_effect = mock_get_by_id

            # act / assert
            with pytest.raises(BizError) as exc:
                await svc.create_assembly(
                    data,
                    pdf_bytes=b"%PDF-1.4 fake",
                    pdf_filename="test.pdf",
                )
            assert exc.value.code == ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN
            assert exc.value.http_status == 400
            mock_serial_counters.acquire_serial.assert_not_awaited()


class TestAssemblySerialRelease:
    """cancel / soft_delete 释放装配体流水号。"""

    async def test_cancel_releases_assembly_serial(self, svc):
        """Assembly.serial_no → None after cancel_assembly."""
        asm = make_assembly(serial_no="L0001", status="IN_PROCESS")
        # Children must be non-terminal so sm.cancel() runs
        child_active = make_part(id=2001, status="IN_PROCESS")
        child_done = make_part(id=2002, status="COMPLETED")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[child_active, child_done])
        svc.drawings.list_for_assembly = AsyncMock(return_value=[])
        svc.part_service._to_out = AsyncMock(return_value=[])

        async def mock_list_by_ids(ids):
            return []
        svc.customers.list_by_ids = mock_list_by_ids

        # act
        await svc.cancel_assembly(1001)

        # assert
        assert asm.serial_no is None, (
            "cancel_assembly 必须把 Assembly.serial_no 置 None 释放槽位"
        )

    async def test_soft_delete_releases_assembly_serial(self, svc):
        """Assembly.serial_no → None after soft_delete_assembly (old 代码不会置 None,
        现在需要置 None 让 partial unique index 把槽位释放)."""
        asm = make_assembly(serial_no="L9999")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[])
        svc.files.list_for_part_ids = AsyncMock(return_value=[])
        svc.files.list_by_assembly = AsyncMock(return_value=[])

        # act
        await svc.soft_delete_assembly(1001)

        # assert
        assert asm.serial_no is None

    async def test_cancel_already_null_serial_skips_release(self, svc):
        """cancel on asm.serial_no is None: no error, no extra op."""
        asm = make_assembly(serial_no=None, status="PENDING")
        # No children
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.parts.list_children = AsyncMock(return_value=[])
        svc.drawings.list_for_assembly = AsyncMock(return_value=[])
        svc.part_service._to_out = AsyncMock(return_value=[])
        async def mock_list_by_ids(ids):
            return []
        svc.customers.list_by_ids = mock_list_by_ids

        # act — should not raise
        await svc.cancel_assembly(1001)

        # assert: serial_no still None
        assert asm.serial_no is None
