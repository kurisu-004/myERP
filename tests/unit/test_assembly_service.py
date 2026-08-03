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
    PartEventRepository,
    PartFileRepository,
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
from schema.part_file import PartFileOut as DrawingFileOut  # 兼容
from schema.part import PartOut
from service.assembly import AssemblyService, _parse_status
from service.part import PartService
from service.part_file import PartFileService as DrawingService  # 兼容 alias

pytestmark = pytest.mark.asyncio


def _patch_split_pdf(monkeypatch: pytest.MonkeyPatch, n_pages: int) -> None:
    """把 service.assembly.split_pdf 替换成返回 n_pages 个单页字节流的桩。

    默认每个 blob 是 b"page-{i}"，避免触发真实 pypdf 解析。
    测试关心的不是 PDF 内容，是「split_pdf 返回 N 段、每段上传一次 COS」这件事。

    split_pdf 本身是同步函数（pypdf 同步 API），所以桩也用 sync。
    """
    splits = [f"page-{i}".encode() for i in range(n_pages)]

    def fake_split(pdf_bytes: bytes) -> list[bytes]:
        return splits

    monkeypatch.setattr("service.assembly.split_pdf", fake_split)


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
        # 2026-07-24 新增：装配体自身价格 + 送货单字段（默认 0 / null）
        quantity=1,
        unit_price=Decimal("0"),
        total_price=Decimal("0"),
        order_no=None,
        system_delivery_date=None,
        note=None,
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
    """Create a MagicMock TPartFile with standard defaults."""
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
    m = MagicMock(spec=PartFileRepository)
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
    # 2026-07-31：soft_delete_assembly 级联批次取消需要 part_batches + outsource_shipments；
    # PartService 实例属性不在 MagicMock(spec=) 自动可见集合里，需显式桩。
    m.part_batches = MagicMock()
    m.part_batches.list_by_part = AsyncMock(return_value=[])
    m.part_batches.update = AsyncMock()
    m.outsource_shipments = MagicMock()
    m.outsource_shipments.get_open_by_batch_id = AsyncMock(return_value=None)
    m.outsource_shipments.update = AsyncMock()
    return m


@pytest.fixture
def mock_drawings():
    m = MagicMock(spec=DrawingService)
    m.list_for_assembly = AsyncMock(return_value=[])
    m.delete_files_silently = AsyncMock()
    m._to_out = AsyncMock()
    m.upload = AsyncMock(return_value=None)  # 测试按需覆盖
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
        part_files=mock_drawings,
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

        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
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

        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
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

        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
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

        # 2026-07-29 批次化：子件取消走 PartService.cancel（级联批次 + rollup）
        svc.part_service.cancel.assert_any_await(child_active.id)
        svc.part_service.cancel.assert_any_await(child_pending.id)
        assert svc.part_service.cancel.await_count == 2
        asm.sm.cancel.assert_called_once()
        assert result.assembly.status == "IN_PROCESS"

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

        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
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
        svc.files.get_master_for_assembly = AsyncMock(return_value=asm_file)

        # Act
        await svc.soft_delete_assembly(1001)

        # Assert
        svc.files.soft_delete_many.assert_awaited_once()
        files_arg = svc.files.soft_delete_many.await_args[0][0]
        assert len(files_arg) == 2  # child + asm files

        svc.parts.soft_delete.assert_awaited_once_with(child)
        svc.assemblies.soft_delete.assert_awaited_once_with(asm)
        svc.part_files.delete_files_silently.assert_awaited_once_with(
            ["key-asm.pdf", "key-child.pdf"]
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
        svc.files.get_master_for_assembly = AsyncMock(return_value=asm_file)

        await svc.soft_delete_assembly(1001)

        svc.files.soft_delete_many.assert_awaited_once()
        files_arg = svc.files.soft_delete_many.await_args[0][0]
        assert len(files_arg) == 1  # only asm file

        svc.parts.soft_delete.assert_not_called()
        svc.assemblies.soft_delete.assert_awaited_once_with(asm)
        svc.part_files.delete_files_silently.assert_awaited_once()


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
                )
            ],
        )

    async def test_empty_pdf_with_children_rejected(self, svc, create_data):
        """无 PDF 但 children 非空 → BIZ_INVALID_VALUE（要在详情页 add_child）。"""
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"", pdf_filename="drawing.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400
        assert "未提供 PDF" in str(exc.value.message)

    async def test_empty_assembly_succeeds(
        self, svc, mock_customers, mock_assemblies,
    ):
        """无 PDF + 无 children → 创建空装配体（PENDING, no serial, no files）。"""
        from schema.assembly import AssemblyCreateRequest

        leaf = make_customer(id=1, name="Sub", parent_id=10)
        async def mock_get_by_id(cid):
            return {1: leaf}.get(cid)
        mock_customers.get_by_id.side_effect = mock_get_by_id
        mock_assemblies.session.flush = AsyncMock()
        mock_assemblies._assembly_to_out = AsyncMock()
        # Make _assembly_to_out work
        from schema.assembly import AssemblyOut
        async def fake_asm_out(asm, child_count):
            return AssemblyOut(
                id=str(asm.id), version=getattr(asm, "version", 0),
                serial_no=asm.serial_no,
                drawing_no=asm.drawing_no, name=asm.name,
                applicant_name=asm.applicant_name, customer_id=str(asm.customer_id),
                customer_name=None, parent_customer_name=None, customer_path=None,
                request_date=asm.request_date, planned_delivery_date=asm.planned_delivery_date,
                actual_delivery_date=None, is_urgent=asm.is_urgent,
                status=asm.status, child_count=child_count,
                created_at=datetime(2026, 7, 1, 10, 0, 0),
                updated_at=datetime(2026, 7, 1, 10, 0, 0),
            )
        mock_assemblies._assembly_to_out = fake_asm_out
        svc._assembly_to_out = fake_asm_out
        svc._broadcast_event = AsyncMock()

        data = AssemblyCreateRequest(
            name="Empty Asm",
            drawing_no="DWG-EMPTY",
            customer_id="1",
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            children=[],
        )

        result = await svc.create_assembly(data, pdf_bytes=None, pdf_filename=None)

        assert result.assembly.status == "PENDING"
        assert result.assembly.serial_no is None
        assert result.children == []
        assert result.files == []
        # 没调 serial counter
        svc.serial_counters.acquire_serial.assert_not_called()

    async def test_non_pdf_filename(self, svc, create_data, monkeypatch):
        """Non-PDF filename -> BizError BIZ_DRAWING_FILE_BAD_TYPE 400."""
        _patch_split_pdf(monkeypatch, n_pages=2)
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"fake content", pdf_filename="drawing.jpg"
            )
        assert exc.value.code == ErrCode.BIZ_PART_FILE_BAD_TYPE
        assert exc.value.http_status == 400

    async def test_customer_not_found(self, svc, create_data, monkeypatch):
        """Customer not found -> BizError BIZ_ASSEMBLY_BAD_CUSTOMER 404.

        新流程：PDF split 校验在 customer 校验之前；这里把 split 桩成「合法 2 页」
        以让流程推进到 customer check。
        """
        _patch_split_pdf(monkeypatch, n_pages=2)  # 1 master + 1 child（与 create_data 匹配）
        svc.customers.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"fake content", pdf_filename="drawing.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER
        assert exc.value.http_status == 404

    async def test_single_page_pdf_rejected(
        self, svc, create_data, monkeypatch
    ):
        """单页 PDF（无子件位）→ BIZ_ASSEMBLY_TOO_MANY_CHILDREN。"""
        _patch_split_pdf(monkeypatch, n_pages=1)
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"%PDF-1.4 fake", pdf_filename="x.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN
        assert exc.value.http_status == 400
        assert "至少 2 页" in str(exc.value.message)

    async def test_zero_page_pdf_rejected(
        self, svc, create_data, monkeypatch
    ):
        """0 页 PDF → BIZ_ASSEMBLY_TOO_MANY_CHILDREN。"""
        _patch_split_pdf(monkeypatch, n_pages=0)
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                create_data, pdf_bytes=b"%PDF-1.4 fake", pdf_filename="x.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN
        assert exc.value.http_status == 400

    async def test_page_count_mismatch(self, svc, monkeypatch):
        """PDF 5 页 + 3 children → BIZ_ASSEMBLY_TOO_MANY_CHILDREN。"""
        _patch_split_pdf(monkeypatch, n_pages=5)  # 应有 4 个子件，但只给 3
        data = AssemblyCreateRequest(
            name="Mismatch", drawing_no="DWG-M", customer_id="1",
            request_date=date(2026, 7, 1), planned_delivery_date=date(2026, 8, 1),
            children=[
                AssemblyChildCreateRequest(drawing_no=f"P{i}", name=f"C{i}", quantity=1)
                for i in range(1, 4)
            ],
        )
        with pytest.raises(BizError) as exc:
            await svc.create_assembly(
                data, pdf_bytes=b"%PDF-1.4 fake", pdf_filename="x.pdf"
            )
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN
        assert exc.value.http_status == 400
        assert "应有 4 个子零件" in str(exc.value.message)
        assert "当前 3 个" in str(exc.value.message)

    async def test_corrupt_pdf_raises(self, svc, create_data):
        """pypdf 解析失败（split_pdf 抛异常）→ BIZ_DRAWING_UPLOAD_FAILED。"""
        # 不 patch split_pdf；注入会导致 PdfReadError 的字节
        with patch(
            "service.assembly.split_pdf",
            side_effect=Exception("corrupt stream"),
        ):
            with pytest.raises(BizError) as exc:
                await svc.create_assembly(
                    create_data, pdf_bytes=b"garbage", pdf_filename="x.pdf"
                )
            assert exc.value.code == ErrCode.BIZ_PART_FILE_UPLOAD_FAILED
            assert exc.value.http_status == 400
            assert "PDF 解析失败" in str(exc.value.message)


# ============================================================
# create_assembly — PDF 按页拆分（成功路径）
# ============================================================


class TestCreateAssemblySplit:
    """create_assembly 把 PDF 拆成 1 主 + N 子件 COS 对象；每个子件独立 key。"""

    @pytest.fixture
    def three_child_data(self):
        return AssemblyCreateRequest(
            name="Split Asm",
            drawing_no="DWG-SPLIT",
            customer_id="1",
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            children=[
                AssemblyChildCreateRequest(
                    drawing_no="PART-A", name="Child A", quantity=1,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="PART-B", name="Child B", quantity=1,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="PART-C", name="Child C", quantity=1,
                ),
            ],
        )

    async def test_writes_one_master_plus_n_child_files(
        self,
        svc,
        three_child_data,
        mock_customers,
        mock_serial_counters,
        mock_parts,
        mock_files,
        mock_drawings,
        monkeypatch,
    ):
        """1 master TPartFile（assembly_id, page_index=None）+ 3 child
        TPartFile（part_id, page_index=None, 各自独立 object_key）。"""
        from schema.drawing import DrawingFileOut

        _patch_split_pdf(monkeypatch, n_pages=4)  # 3 children + 1 master

        with patch("service.part_file.cos_mod.upload_object", new=AsyncMock()) as upload_mock:
            # arrange
            leaf = make_customer(id=1, name="Luda Sub", parent_id=10)
            parent = make_customer(id=10, name="路达", parent_id=None)

            async def mock_get_by_id(cid):
                return {1: leaf, 10: parent}.get(cid)
            mock_customers.get_by_id.side_effect = mock_get_by_id
            mock_serial_counters.acquire_serial.return_value = "L1067"

            # 记录所有 upload 调用
            upload_calls = mock_drawings.upload.call_args_list  # 占位：assert 阶段再取

            async def mock_create_part(p):
                return p
            mock_parts.create.side_effect = mock_create_part

            stub_file = DrawingFileOut(
                id="1", version=0, owner_id="1",
                file_type="PDF", original_filename="x.pdf", file_size=10, kind="DRAWING",
                content_type="application/pdf", 
                download_url="https://example.com/x", upload_status="READY",
                created_at=datetime(2026, 7, 1, 10, 0, 0),
            )
            mock_drawings._to_out = AsyncMock(return_value=stub_file)
            mock_drawings.upload = AsyncMock(return_value=stub_file)
            svc._assembly_to_out = AsyncMock(return_value=MagicMock(spec=AssemblyOut))
            svc.part_service._to_out = AsyncMock(return_value=[])

            # act
            await svc.create_assembly(
                three_child_data,
                pdf_bytes=b"%PDF-1.4 fake",
                pdf_filename="x.pdf",
            )

            # assert: 4 个 TPartFile 创建（1 master kind=ASSEMBLY_MASTER + 3 children kind=DRAWING）
            upload_calls = mock_drawings.upload.call_args_list
            upload_kinds = [c.kwargs.get("kind") for c in upload_calls]
            assert upload_kinds.count("ASSEMBLY_MASTER") == 1, upload_kinds
            assert upload_kinds.count("DRAWING") == 3, upload_kinds
            assert len(upload_calls) == 4

            # master 用 assembly.id 作为 polymorphic part_id
            master_call = next(c for c in upload_calls if c.kwargs.get("kind") == "ASSEMBLY_MASTER")
            assert master_call.kwargs.get("owner_id") is not None
            # master owner_id == assembly.id (real new_id)

            # children 用 child_id 作为 part_id
            child_calls = [c for c in upload_calls if c.kwargs.get("kind") == "DRAWING"]
            assert len(child_calls) == 3
            for cc in child_calls:
                assert cc.kwargs.get("owner_id") is not None

            # COS upload 调用：1 master + 3 children = 4 次
            # 注：mock_drawings.upload 是 PartFileService 的上传入口，
            # 测试桩直接 return stub_file，所以真实 cos_mod.upload_object 没被调到；
            # 用 mock_drawings.upload.call_count 替代 upload_mock.await_count。
            assert mock_drawings.upload.call_count == 4

    async def test_child_cos_keys_under_part_prefix(
        self,
        svc,
        three_child_data,
        mock_customers,
        mock_serial_counters,
        mock_parts,
        mock_files,
        mock_drawings,
        monkeypatch,
    ):
        """每个子件 COS key 形如 drawings/part/{child_id}/{file_id}.pdf。"""
        from schema.drawing import DrawingFileOut

        _patch_split_pdf(monkeypatch, n_pages=4)

        with patch("service.part_file.cos_mod.upload_object", new=AsyncMock()):
            leaf = make_customer(id=1, name="Luda Sub", parent_id=10)
            parent = make_customer(id=10, name="路达", parent_id=None)

            async def mock_get_by_id(cid):
                return {1: leaf, 10: parent}.get(cid)
            mock_customers.get_by_id.side_effect = mock_get_by_id
            mock_serial_counters.acquire_serial.return_value = "L0001"

            uploaded_keys: list[str] = []

            async def mock_create_part(p):
                return p
            mock_parts.create.side_effect = mock_create_part

            async def capture_upload_call(**kwargs):
                uploaded_keys.append((kwargs.get("kind"), kwargs.get("owner_id")))
                return stub_file  # 给上层 part_files.upload 返回 stub_file

            stub_file = DrawingFileOut(
                id="1", version=0, owner_id="1",
                file_type="PDF", original_filename="x.pdf", file_size=10, kind="DRAWING",
                content_type="application/pdf",
                download_url="https://example.com/x", upload_status="READY",
                created_at=datetime(2026, 7, 1, 10, 0, 0),
            )
            mock_drawings.upload.side_effect = capture_upload_call
            mock_drawings._to_out = AsyncMock(return_value=stub_file)
            svc._assembly_to_out = AsyncMock(return_value=MagicMock(spec=AssemblyOut))
            svc.part_service._to_out = AsyncMock(return_value=[])

            await svc.create_assembly(
                three_child_data,
                pdf_bytes=b"%PDF-1.4 fake",
                pdf_filename="x.pdf",
            )

            # 1 master + 3 child = 4 uploads
            assert len(uploaded_keys) == 4
            # 第一个是 master（ASSEMBLY_MASTER kind）
            kinds = [k for k, _ in uploaded_keys]
            assert kinds[0] == "ASSEMBLY_MASTER"
            assert kinds.count("DRAWING") == 3

    async def test_unit_price_and_total_price_default_zero(
        self,
        svc,
        three_child_data,
        mock_customers,
        mock_serial_counters,
        mock_parts,
        mock_files,
        mock_drawings,
        monkeypatch,
    ):
        """TPart.unit_price / total_price = 0（不再由前端传入）。"""
        from schema.drawing import DrawingFileOut

        _patch_split_pdf(monkeypatch, n_pages=4)

        with patch("service.part_file.cos_mod.upload_object", new=AsyncMock()):
            leaf = make_customer(id=1, name="Luda Sub", parent_id=10)
            parent = make_customer(id=10, name="路达", parent_id=None)
            async def mock_get_by_id(cid):
                return {1: leaf, 10: parent}.get(cid)
            mock_customers.get_by_id.side_effect = mock_get_by_id
            mock_serial_counters.acquire_serial.return_value = "L0001"

            captured_parts: list[TPart] = []

            async def mock_create_part(p):
                captured_parts.append(p)
                return p
            mock_parts.create.side_effect = mock_create_part

            stub_file = DrawingFileOut(
                id="1", version=0, owner_id="1",
                file_type="PDF", original_filename="x.pdf", file_size=10, kind="DRAWING",
                content_type="application/pdf", 
                download_url="https://example.com/x", upload_status="READY",
                created_at=datetime(2026, 7, 1, 10, 0, 0),
            )
            mock_drawings._to_out = AsyncMock(return_value=stub_file)
            mock_drawings.upload = AsyncMock(return_value=stub_file)
            svc._assembly_to_out = AsyncMock(return_value=MagicMock(spec=AssemblyOut))
            svc.part_service._to_out = AsyncMock(return_value=[])

            await svc.create_assembly(
                three_child_data,
                pdf_bytes=b"%PDF-1.4 fake",
                pdf_filename="x.pdf",
            )

            assert len(captured_parts) == 3
            for p in captured_parts:
                assert p.unit_price == Decimal("0")
                assert p.total_price == Decimal("0")

    async def test_create_assembly_with_unit_price_calculates_total_price(
        self,
        svc,
        three_child_data,
        mock_customers,
        mock_serial_counters,
        mock_parts,
        mock_files,
        mock_drawings,
        monkeypatch,
    ):
        """装配体新建时显式传 quantity + unit_price → total_price = 数量 × 单价。

        2026-07-24 PR：装配件本身需可设置价格，total_price 不传时按 unit_price*quantity 计算。
        """
        from schema.drawing import DrawingFileOut

        _patch_split_pdf(monkeypatch, n_pages=4)

        with patch("service.part_file.cos_mod.upload_object", new=AsyncMock()):
            leaf = make_customer(id=1, name="Luda Sub", parent_id=10)
            parent = make_customer(id=10, name="路达", parent_id=None)
            async def mock_get_by_id(cid):
                return {1: leaf, 10: parent}.get(cid)
            mock_customers.get_by_id.side_effect = mock_get_by_id
            mock_serial_counters.acquire_serial.return_value = "L0002"

            # 设置 quantity=2, unit_price=300 → 期望 total_price=600
            three_child_data.quantity = 2
            three_child_data.unit_price = Decimal("300")
            three_child_data.total_price = None  # 让 service 自动算

            captured_assembly: list = []

            async def mock_create_asm(asm):
                captured_assembly.append(asm)
                return asm
            svc.assemblies.create.side_effect = mock_create_asm

            stub_file = DrawingFileOut(
                id="1", version=0, owner_id="1",
                file_type="PDF", original_filename="x.pdf", file_size=10, kind="DRAWING",
                content_type="application/pdf",
                download_url="https://example.com/x", upload_status="READY",
                created_at=datetime(2026, 7, 1, 10, 0, 0),
            )
            mock_drawings.upload = AsyncMock(return_value=stub_file)
            svc._assembly_to_out = AsyncMock(return_value=MagicMock(spec=AssemblyOut))
            svc.part_service._to_out = AsyncMock(return_value=[])

            await svc.create_assembly(
                three_child_data,
                pdf_bytes=b"%PDF-1.4 fake",
                pdf_filename="x.pdf",
            )

            assert len(captured_assembly) == 1
            asm = captured_assembly[0]
            assert asm.quantity == 2
            assert asm.unit_price == Decimal("300")
            assert asm.total_price == Decimal("600")

    async def test_update_assembly_set_total_price_clears_children_prices(
        self,
        svc,
        mock_customers,
        mock_parts,
        mock_files,
        monkeypatch,
    ):
        """装配体设置总价 > 0 时，主动清零所有 active 子件的 unit_price/total_price。

        2026-07-24 PR：业务约束"装配体已设价时子件不能再设价"的 server-side 兜底。
        """
        from decimal import Decimal
        from model import TPart
        from schema.assembly import AssemblyUpdateRequest

        # 一个 active 装配件，已有 3 个子件，其中 2 个 unit_price > 0
        from model import TAssembly

        existing_asm = TAssembly(
            id=100,
            drawing_no="D1", name="N1", customer_id=1,
            request_date=date(2026, 7, 24), planned_delivery_date=date(2026, 8, 24),
            is_urgent=False, status="PENDING", serial_no="L0001",
            quantity=1, unit_price=Decimal("0"), total_price=Decimal("0"),
        )
        existing_asm.version = 0
        svc.assemblies.get_by_id = AsyncMock(return_value=existing_asm)

        children = [
            TPart(id=1, drawing_no="C1", customer_id=1, quantity=1,
                  unit_price=Decimal("100"), total_price=Decimal("100"),
                  request_date=date(2026, 7, 24), planned_delivery_date=date(2026, 8, 24),
                  name="c1", applicant_name="x", status="PENDING",
                  is_urgent=False, assembly_id=100),
            TPart(id=2, drawing_no="C2", customer_id=1, quantity=1,
                  unit_price=Decimal("200"), total_price=Decimal("200"),
                  request_date=date(2026, 7, 24), planned_delivery_date=date(2026, 8, 24),
                  name="c2", applicant_name="x", status="PENDING",
                  is_urgent=False, assembly_id=100),
            TPart(id=3, drawing_no="C3", customer_id=1, quantity=1,
                  unit_price=Decimal("0"), total_price=Decimal("0"),
                  request_date=date(2026, 7, 24), planned_delivery_date=date(2026, 8, 24),
                  name="c3", applicant_name="x", status="PENDING",
                  is_urgent=False, assembly_id=100),
        ]
        mock_parts.list_children = AsyncMock(return_value=children)

        updated: list = []
        async def mock_update_part(p):
            updated.append(p)
            return p
        mock_parts.update.side_effect = mock_update_part
        svc.assemblies.update = AsyncMock(return_value=existing_asm)
        svc._broadcast = AsyncMock()
        svc._build_detail = AsyncMock()
        svc._broadcast_event = AsyncMock()

        # 触发 update_assembly，传 total_price=500
        payload = AssemblyUpdateRequest(total_price=Decimal("500"))
        await svc.update_assembly(100, payload)

        # 验证：装配体总价被设为 500
        assert existing_asm.total_price == Decimal("500")
        # 验证：2 个原本有价的子件被清零；第 3 个本来 0 价，未变动
        assert len(updated) == 2
        assert children[0].unit_price == Decimal("0")
        assert children[0].total_price == Decimal("0")
        assert children[1].unit_price == Decimal("0")
        assert children[1].total_price == Decimal("0")

    async def test_update_assembly_set_total_price_zero_keeps_children_prices(
        self,
        svc,
        mock_customers,
        mock_parts,
        monkeypatch,
    ):
        """装配体显式清零总价（= 0）→ 不动子件价格。

        反向验证：清零总价不等于"放开子件定价"，子件原样保留。
        """
        from decimal import Decimal
        from model import TPart, TAssembly
        from schema.assembly import AssemblyUpdateRequest

        existing_asm = TAssembly(
            id=200, drawing_no="D2", name="N2", customer_id=1,
            request_date=date(2026, 7, 24), planned_delivery_date=date(2026, 8, 24),
            is_urgent=False, status="PENDING", serial_no="L0002",
            quantity=1, unit_price=Decimal("0"), total_price=Decimal("500"),
        )
        existing_asm.version = 0
        svc.assemblies.get_by_id = AsyncMock(return_value=existing_asm)
        svc.assemblies.update = AsyncMock(return_value=existing_asm)

        child = TPart(
            id=10, drawing_no="CX", customer_id=1, quantity=1,
            unit_price=Decimal("300"), total_price=Decimal("300"),
            request_date=date(2026, 7, 24), planned_delivery_date=date(2026, 8, 24),
            name="cx", applicant_name="x", status="PENDING",
            is_urgent=False, assembly_id=200,
        )
        mock_parts.list_children = AsyncMock(return_value=[child])

        updated: list = []
        async def mock_update_part(p):
            updated.append(p)
            return p
        mock_parts.update.side_effect = mock_update_part
        svc._broadcast = AsyncMock()
        svc._build_detail = AsyncMock()
        svc._broadcast_event = AsyncMock()

        payload = AssemblyUpdateRequest(total_price=Decimal("0"))
        await svc.update_assembly(200, payload)

        # 装配体总价 = 0；子件价不变（仍未被 update）
        assert existing_asm.total_price == Decimal("0")
        assert len(updated) == 0
        assert child.unit_price == Decimal("300")
        assert child.total_price == Decimal("300")


# ============================================================
# _parse_status (standalone function)
# ============================================================


class TestParseStatus:
    """_parse_status(value: str | None) -> str | None."""

    async def test_none(self):
        assert _parse_status(None) is None

    async def test_valid(self):
        # Exact match — 2026-08-03 7 态扩展
        assert _parse_status("PENDING") == "PENDING"
        assert _parse_status("IN_PROCESS") == "IN_PROCESS"
        assert _parse_status("INSPECTION") == "INSPECTION"
        assert _parse_status("READY_TO_SHIP") == "READY_TO_SHIP"
        assert _parse_status("DELIVERED") == "DELIVERED"
        assert _parse_status("COMPLETED") == "COMPLETED"
        assert _parse_status("CANCELLED") == "CANCELLED"
        # Case-insensitive
        assert _parse_status("in_process") == "IN_PROCESS"
        assert _parse_status("In_Process") == "IN_PROCESS"
        assert _parse_status("inspection") == "INSPECTION"
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
                    drawing_no="PART-A", name="Child A", quantity=1,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="PART-B", name="Child B", quantity=1,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="PART-C", name="Child C", quantity=1,
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
        monkeypatch,
    ):
        """acquire_serial called exactly ONCE; children get derived serials."""
        from schema.drawing import DrawingFileOut

        _patch_split_pdf(monkeypatch, n_pages=4)  # 3 子件 + 1 总图
        with patch("service.part_file.cos_mod.upload_object", new=AsyncMock()):
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
                version=0,
                owner_id="1",
                kind="ASSEMBLY_MASTER",
                file_type="PDF",
                original_filename="test.pdf",
                file_size=10,
                content_type="application/pdf",
                download_url="https://example.com/x",
                upload_status="READY",
                created_at=datetime(2026, 7, 1, 10, 0, 0),
            )
            mock_drawings._to_out = AsyncMock(return_value=stub_file)
            mock_drawings.upload = AsyncMock(return_value=stub_file)
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
        monkeypatch,
    ):
        """100+ children → BizError BIZ_ASSEMBLY_TOO_MANY_CHILDREN (no counter touched)."""
        _patch_split_pdf(monkeypatch, n_pages=101)  # 100 子件 + 1 总图
        with patch("service.part_file.cos_mod.upload_object", new=AsyncMock()):
            # arrange: 100 children
            children = [
                AssemblyChildCreateRequest(
                    drawing_no=f"P{i}", name=f"C{i}", quantity=1,
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
        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
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
        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
        svc.part_service._to_out = AsyncMock(return_value=[])
        async def mock_list_by_ids(ids):
            return []
        svc.customers.list_by_ids = mock_list_by_ids

        # act — should not raise
        await svc.cancel_assembly(1001)

        # assert: serial_no still None
        assert asm.serial_no is None


# ============================================================
# update_assembly  (2026-07-11 接入)
# ============================================================


class TestUpdateAssembly:
    """update_assembly(assembly_id, data) -> AssemblyDetail.
    
    field-level partial update；所有字段可选。仅改 payload 非 None 字段。
    终态（CANCELLED / COMPLETED）拒绝；customer_id 必须叶子节点。
    """

    async def test_partial_name_only(self, svc):
        """只传 name -> 只更新 name，其他字段不变。"""
        asm = make_assembly(id=1001, name="原名", status="PENDING")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        # _build_detail 内部依赖
        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
        svc.parts.list_children = AsyncMock(return_value=[])
        svc.part_service._to_out = AsyncMock(return_value=[])

        from schema.assembly import AssemblyUpdateRequest

        data = AssemblyUpdateRequest(name="新名")
        result = await svc.update_assembly(1001, data)

        assert asm.name == "新名"
        assert asm.drawing_no == "DWG-001"
        assert asm.is_urgent is False  # 默认值未变

    async def test_all_fields_accepted(self, svc):
        """传所有字段都生效。customer_id 是叶子节点。"""
        asm = make_assembly(id=1002, status="PENDING", customer_id=2)
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
        svc.parts.list_children = AsyncMock(return_value=[])
        svc.part_service._to_out = AsyncMock(return_value=[])
        # 叶子客户（有 parent_id）
        leaf = make_customer(id=2, name="Leaf", parent_id=10)
        svc.customers.get_by_id = AsyncMock(return_value=leaf)

        from schema.assembly import AssemblyUpdateRequest

        data = AssemblyUpdateRequest(
            drawing_no="NEW-DRW-001",
            name="新装配体",
            customer_id="2",
            applicant_name="王某",
            request_date=date(2026, 8, 1),
            planned_delivery_date=date(2026, 8, 15),
            actual_delivery_date=date(2026, 8, 14),
            is_urgent=True,
        )
        await svc.update_assembly(1002, data)

        assert asm.drawing_no == "NEW-DRW-001"
        assert asm.name == "新装配体"
        assert asm.customer_id == 2
        assert asm.applicant_name == "王某"
        assert asm.request_date == date(2026, 8, 1)
        assert asm.planned_delivery_date == date(2026, 8, 15)
        assert asm.actual_delivery_date == date(2026, 8, 14)
        assert asm.is_urgent is True

    async def test_terminal_cancelled_rejected(self, svc):
        """CANCELLED 状态拒绝 -> BIZ_INVALID_TRANSITION 400。"""
        asm = make_assembly(status="CANCELLED")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)

        from schema.assembly import AssemblyUpdateRequest

        with pytest.raises(BizError) as exc:
            await svc.update_assembly(1003, AssemblyUpdateRequest(name="x"))
        assert exc.value.code == ErrCode.BIZ_INVALID_TRANSITION
        assert exc.value.http_status == 400

    async def test_terminal_completed_rejected(self, svc):
        """COMPLETED 状态拒绝 -> BIZ_INVALID_TRANSITION 400。"""
        asm = make_assembly(status="COMPLETED")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)

        from schema.assembly import AssemblyUpdateRequest

        with pytest.raises(BizError) as exc:
            await svc.update_assembly(1004, AssemblyUpdateRequest(name="x"))
        assert exc.value.code == ErrCode.BIZ_INVALID_TRANSITION

    async def test_customer_must_be_leaf(self, svc):
        """customer_id 是一级客户（parent_id is NULL） -> 400 BAD_CUSTOMER。"""
        asm = make_assembly(status="PENDING", customer_id=2)
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        # 一级客户，parent_id=None
        root = make_customer(id=99, name="RootCustomer", parent_id=None)
        svc.customers.get_by_id = AsyncMock(return_value=root)

        from schema.assembly import AssemblyUpdateRequest

        with pytest.raises(BizError) as exc:
            await svc.update_assembly(
                1005, AssemblyUpdateRequest(customer_id="99"),
            )
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER
        assert exc.value.http_status == 400

    async def test_invalid_customer_id_string(self, svc):
        """customer_id 是非数字字符串 -> BIZ_INVALID_VALUE 400。"""
        asm = make_assembly(status="PENDING")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)

        from schema.assembly import AssemblyUpdateRequest

        with pytest.raises(BizError) as exc:
            await svc.update_assembly(
                1006, AssemblyUpdateRequest(customer_id="not-a-number"),
            )
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE

    async def test_not_found(self, svc):
        """装配体不存在 -> 404。"""
        svc.assemblies.get_by_id = AsyncMock(return_value=None)

        from schema.assembly import AssemblyUpdateRequest

        with pytest.raises(BizError) as exc:
            await svc.update_assembly(9999, AssemblyUpdateRequest(name="x"))
        assert exc.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_strip_whitespace(self, svc):
        """drawing_no / name / applicant_name 自动 strip 首尾空格。"""
        asm = make_assembly(status="PENDING")
        svc.assemblies.get_by_id = AsyncMock(return_value=asm)
        svc.part_files.list_for_assembly = AsyncMock(return_value=[])
        svc.parts.list_children = AsyncMock(return_value=[])
        svc.part_service._to_out = AsyncMock(return_value=[])

        from schema.assembly import AssemblyUpdateRequest

        await svc.update_assembly(
            1007,
            AssemblyUpdateRequest(
                drawing_no="  DRW-XYZ  ",
                name="\t名称  ",
                applicant_name=" 申请人 ",
            ),
        )
        assert asm.drawing_no == "DRW-XYZ"
        assert asm.name == "名称"
        assert asm.applicant_name == "申请人"
