"""PartService.list_parts include_assemblies 合并展示单元测试（2026-07-30）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import configure_mappers

configure_mappers()
from model import TAssembly, TCustomer, TPart
from model.enums import PartSortKey, PartStatus, SortDir, AssemblyStatus
from schema.part import PartListQuery
from service.part import PartService
from tests.unit._fake_batches import FakePartBatchRepository

pytestmark = pytest.mark.asyncio


def _make_part(id: int, customer_id: int, **kwargs) -> TPart:
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
        assembly_id=kwargs.get("assembly_id"),
        created_at=kwargs.get("created_at", datetime(2025, 1, 1, 0, 0, 0)),
    )


def _make_assembly(id: int, customer_id: int, **kwargs) -> TAssembly:
    return TAssembly(
        id=id,
        customer_id=customer_id,
        serial_no=kwargs.get("serial_no", "L2507"),
        name=kwargs.get("name", "Test Assembly"),
        drawing_no=kwargs.get("drawing_no", "DWG-ASM-001"),
        applicant_name=kwargs.get("applicant_name", "Applicant"),
        quantity=kwargs.get("quantity", 1),
        unit_price=kwargs.get("unit_price", Decimal("200")),
        total_price=kwargs.get("total_price", Decimal("200")),
        request_date=kwargs.get("request_date", date(2025, 1, 1)),
        planned_delivery_date=kwargs.get("planned_delivery_date", date(2025, 2, 1)),
        actual_delivery_date=kwargs.get("actual_delivery_date"),
        status=kwargs.get("status", AssemblyStatus.PENDING.value),
        is_urgent=kwargs.get("is_urgent", False),
        created_at=kwargs.get("created_at", datetime(2025, 1, 1, 0, 0, 0)),
    )


def _make_customer(id: int, name: str, parent_id: int | None = None) -> TCustomer:
    return TCustomer(id=id, name=name, parent_id=parent_id, serial_prefix="L")


@pytest.fixture
def mock_parts() -> AsyncMock:
    mock = AsyncMock()
    mock.list_with_filters = AsyncMock()
    mock.count_with_filters = AsyncMock()
    mock.list_by_ids = AsyncMock()
    mock.list_children = AsyncMock()
    mock.session = MagicMock()
    mock.session.execute = AsyncMock()
    return mock


@pytest.fixture
def mock_customers() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.list_by_ids = AsyncMock()
    mock.list_children = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_assemblies() -> AsyncMock:
    mock = AsyncMock()
    mock.list_with_filters = AsyncMock()
    mock.count_with_filters = AsyncMock()
    return mock


@pytest.fixture
def service(mock_parts, mock_customers, mock_assemblies) -> PartService:
    return PartService(
        parts=mock_parts,
        part_batches=FakePartBatchRepository(parts_provider=mock_parts.get_by_id),
        customers=mock_customers,
        workers=AsyncMock(),
        events=AsyncMock(),
        serial_counters=AsyncMock(),
        shelves=AsyncMock(),
        assemblies=mock_assemblies,
        processes=AsyncMock(),
    )


class TestListPartsIncludeAssemblies:
    """GET /parts?include_assemblies=true 合并列表 service 层测试。"""

    async def test_include_assemblies_false_uses_existing_behavior(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """默认 False：不查装配件，不走合并逻辑。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        query = PartListQuery(include_assemblies=False)
        result = await service.list_parts(query)

        mock_assemblies.list_with_filters.assert_not_awaited()
        assert len(result.items) == 1
        assert result.items[0].row_type == "PART"
        assert result.total == 1

    async def test_include_assemblies_returns_both_types(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """True 时返回零件 + 装配件，row_type 正确，子件不在顶层。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        # 零件查询排除子件
        part = _make_part(id=1, customer_id=10, assembly_id=None)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        asm = _make_assembly(id=100, customer_id=10)
        mock_assemblies.list_with_filters.return_value = [asm]
        mock_assemblies.count_with_filters.return_value = 1

        # mock child count query
        from sqlalchemy.engine import Result
        result_mock = MagicMock(spec=Result)
        result_mock.all.return_value = [(100, 2)]
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(include_assemblies=True, limit=50, offset=0)
        result = await service.list_parts(query)

        assert result.total == 2
        assert len(result.items) == 2
        types = {item.row_type for item in result.items}
        assert types == {"PART", "ASSEMBLY"}

        # 装配件行字段
        asm_item = next(i for i in result.items if i.row_type == "ASSEMBLY")
        assert asm_item.has_children is True
        assert asm_item.child_count == 2
        assert asm_item.location is None
        assert asm_item.shelf_code is None

    async def test_status_filter_mapping_skips_assemblies_when_no_overlap(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """statuses=[PROGRAMMING] 时装配件集为空（无匹配状态）。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10, status=PartStatus.PROGRAMMING.value)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        query = PartListQuery(
            include_assemblies=True,
            statuses=[PartStatus.PROGRAMMING],
        )
        result = await service.list_parts(query)

        mock_assemblies.list_with_filters.assert_not_awaited()
        assert result.total == 1
        assert result.items[0].row_type == "PART"

    async def test_cross_type_sorting_by_date(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """按 planned_delivery_date 跨类型排序正确。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(
            id=1, customer_id=10,
            planned_delivery_date=date(2025, 3, 1),
            created_at=datetime(2025, 1, 1),
        )
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        asm = _make_assembly(
            id=100, customer_id=10,
            planned_delivery_date=date(2025, 2, 1),
            created_at=datetime(2025, 1, 2),
        )
        mock_assemblies.list_with_filters.return_value = [asm]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True,
            sort_by=PartSortKey.PLANNED_DELIVERY_DATE,
            sort_dir=SortDir.ASC,
        )
        result = await service.list_parts(query)

        # ASC: assembly (2025-02-01) before part (2025-03-01)
        assert result.items[0].row_type == "ASSEMBLY"
        assert result.items[1].row_type == "PART"

    async def test_pagination_total_is_sum(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """total = 零件数 + 装配件数。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        mock_parts.list_with_filters.return_value = []
        mock_parts.count_with_filters.return_value = 3
        mock_assemblies.list_with_filters.return_value = []
        mock_assemblies.count_with_filters.return_value = 2

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(include_assemblies=True)
        result = await service.list_parts(query)

        assert result.total == 5
