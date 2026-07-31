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
        # 2026-07-31：装配件补齐字段（PR-F 与 Bug 1）
        order_no=kwargs.get("order_no"),
        system_delivery_date=kwargs.get("system_delivery_date"),
        note=kwargs.get("note"),
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

    # ===== 2026-07-31：装配件补齐订单号/日期区间筛选（Bug 1）=====
    # list_parts 并入装配件时，应把 order_no / request_date / planned_delivery_date
    # / system_delivery_date 透传给 AssemblyRepository；has_outsource_history=True
    # 时装配件整段被排除（装配体本身不外协，外协走 t_part）。

    async def test_order_no_filter_applies_to_assemblies(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """传 order_no 时，service 透传给 AssemblyRepository.list_with_filters
        与 count_with_filters 的 order_no_like 参数。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        asm = _make_assembly(
            id=100, customer_id=10, order_no="PO-2025-001"
        )
        mock_assemblies.list_with_filters.return_value = [asm]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True, order_no="PO-2025-001"
        )
        result = await service.list_parts(query)

        # list/count 都被调用
        mock_assemblies.list_with_filters.assert_awaited()
        mock_assemblies.count_with_filters.assert_awaited()
        # 透传 order_no_like
        list_kwargs = mock_assemblies.list_with_filters.await_args.kwargs
        count_kwargs = mock_assemblies.count_with_filters.await_args.kwargs
        assert list_kwargs["order_no_like"] == "PO-2025-001"
        assert count_kwargs["order_no_like"] == "PO-2025-001"
        # 返回的装配体行带 order_no
        asm_item = next(i for i in result.items if i.row_type == "ASSEMBLY")
        assert asm_item.order_no == "PO-2025-001"

    async def test_request_date_range_applies_to_assemblies(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """request_date_from/to 透传给装配体查询，并按区间收窄。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(
            id=1, customer_id=10, request_date=date(2025, 3, 1)
        )
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        # 装配体在区间内 → 应被返回
        asm_in_range = _make_assembly(
            id=100, customer_id=10, request_date=date(2025, 3, 15)
        )
        mock_assemblies.list_with_filters.return_value = [asm_in_range]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True,
            request_date_from=date(2025, 3, 1),
            request_date_to=date(2025, 3, 31),
        )
        result = await service.list_parts(query)

        # 透传 request_date_from/to
        list_kwargs = mock_assemblies.list_with_filters.await_args.kwargs
        count_kwargs = mock_assemblies.count_with_filters.await_args.kwargs
        assert list_kwargs["request_date_from"] == date(2025, 3, 1)
        assert list_kwargs["request_date_to"] == date(2025, 3, 31)
        assert count_kwargs["request_date_from"] == date(2025, 3, 1)
        assert count_kwargs["request_date_to"] == date(2025, 3, 31)
        # 区间内的装配体行被返回
        asm_items = [i for i in result.items if i.row_type == "ASSEMBLY"]
        assert len(asm_items) == 1
        assert asm_items[0].request_date == date(2025, 3, 15)

    async def test_planned_delivery_date_range_applies_to_assemblies(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """planned_delivery_date_from/to 透传给装配体查询。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        asm = _make_assembly(
            id=100, customer_id=10,
            planned_delivery_date=date(2025, 4, 10),
        )
        mock_assemblies.list_with_filters.return_value = [asm]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True,
            planned_delivery_date_from=date(2025, 4, 1),
            planned_delivery_date_to=date(2025, 4, 30),
        )
        result = await service.list_parts(query)

        list_kwargs = mock_assemblies.list_with_filters.await_args.kwargs
        count_kwargs = mock_assemblies.count_with_filters.await_args.kwargs
        assert list_kwargs["planned_delivery_date_from"] == date(2025, 4, 1)
        assert list_kwargs["planned_delivery_date_to"] == date(2025, 4, 30)
        assert count_kwargs["planned_delivery_date_from"] == date(2025, 4, 1)
        assert count_kwargs["planned_delivery_date_to"] == date(2025, 4, 30)
        # 区间内的装配体行被返回
        asm_items = [i for i in result.items if i.row_type == "ASSEMBLY"]
        assert len(asm_items) == 1
        assert asm_items[0].planned_delivery_date == date(2025, 4, 10)

    async def test_system_delivery_date_range_applies_to_assemblies(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """system_delivery_date_from/to 透传给装配体查询；NULL 兜底语义
        由 AssemblyRepository._build_filter_stmt 实现（与 PartRepository 对齐）。

        本测试同时验证：
        - 区间内的装配件被返回；
        - 至少 1 个 NULL system_delivery_date 的装配件也应被命中（NULL 兜底）；
        - query 字段被透传。
        """
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        # 一条在区间内、一条 system_delivery_date=NULL（区间内也应命中）
        asm_in_range = _make_assembly(
            id=100, customer_id=10,
            system_delivery_date=date(2025, 5, 10),
        )
        asm_null = _make_assembly(
            id=101, customer_id=10,
            system_delivery_date=None,
        )
        mock_assemblies.list_with_filters.return_value = [asm_in_range, asm_null]
        mock_assemblies.count_with_filters.return_value = 2

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True,
            system_delivery_date_from=date(2025, 5, 1),
            system_delivery_date_to=date(2025, 5, 31),
        )
        result = await service.list_parts(query)

        # 透传 system_delivery_date_from/to
        list_kwargs = mock_assemblies.list_with_filters.await_args.kwargs
        count_kwargs = mock_assemblies.count_with_filters.await_args.kwargs
        assert list_kwargs["system_delivery_date_from"] == date(2025, 5, 1)
        assert list_kwargs["system_delivery_date_to"] == date(2025, 5, 31)
        assert count_kwargs["system_delivery_date_from"] == date(2025, 5, 1)
        assert count_kwargs["system_delivery_date_to"] == date(2025, 5, 31)
        # 至少 1 个 NULL system_delivery_date 的装配件被命中
        asm_items = [i for i in result.items if i.row_type == "ASSEMBLY"]
        assert len(asm_items) == 2
        null_count = sum(1 for a in asm_items if a.system_delivery_date is None)
        assert null_count >= 1

    async def test_has_outsource_history_true_excludes_assemblies(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """has_outsource_history=True 时，装配件整段被排除（装配体不外协）。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10)
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        # 即便装配体 mock 有数据，也不会被调用
        mock_assemblies.list_with_filters.return_value = [
            _make_assembly(id=100, customer_id=10)
        ]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True, has_outsource_history=True
        )
        result = await service.list_parts(query)

        # 装配体仓储不应被调用
        mock_assemblies.list_with_filters.assert_not_awaited()
        mock_assemblies.count_with_filters.assert_not_awaited()
        # 返回的行全部是 PART
        assert all(i.row_type == "PART" for i in result.items)
        # total 等于零件 count
        assert result.total == 1

    # ===== 2026-07-31：序列号独立搜索（带出母装配件） =====

    async def test_serial_no_filter_brings_parent_assembly_via_children(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """搜子件序列号（如 'L1067-03'）时，service 透传 serial_no 给装配件仓储，
        让 repository 用 EXISTS 子件命中带出母装配件行。

        本测锁定 service 层的 plumbing（透传参数 + list/count 都被调用）；
        EXISTS 谓词本身由 repository 集成测试保证。

        装配件整段在 has_outsource_history=False 时正常调用；
        子件从顶层隐藏由 `assembly_id_is_null=True` 谓词在 repository 层处理
        （mock 直接返回的子件会被 service 透传给前端，所以本测只锁定参数透传）。
        """
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        # 独立零件查询：serial_no 命中子件（mock 直接返回子件）
        child = _make_part(id=1, customer_id=10, serial_no="L1067-03")
        mock_parts.list_with_filters.return_value = [child]
        mock_parts.count_with_filters.return_value = 1

        # 装配件仓储通过 EXISTS 子件命中（serial_no='L1067-03'）
        parent_asm = _make_assembly(id=100, customer_id=10, serial_no="L1067")
        mock_assemblies.list_with_filters.return_value = [parent_asm]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = [(100, 1)]  # child_count
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(include_assemblies=True, serial_no="L1067-03")
        result = await service.list_parts(query)

        # serial_no 透传给装配件仓储
        list_kwargs = mock_assemblies.list_with_filters.await_args.kwargs
        count_kwargs = mock_assemblies.count_with_filters.await_args.kwargs
        assert list_kwargs["serial_no_like"] == "L1067-03"
        assert count_kwargs["serial_no_like"] == "L1067-03"

        # serial_no 透传给零件仓储
        part_list_kwargs = mock_parts.list_with_filters.await_args.kwargs
        part_count_kwargs = mock_parts.count_with_filters.await_args.kwargs
        assert part_list_kwargs["serial_no"] == "L1067-03"
        assert part_count_kwargs["serial_no"] == "L1067-03"

        # 母装配件行被返回（通过 EXISTS 子件命中 → mock 返回了 parent_asm）
        asm_items = [i for i in result.items if i.row_type == "ASSEMBLY"]
        assert len(asm_items) == 1
        assert asm_items[0].serial_no == "L1067"

        # 独立子件会被 service._to_list_out 处理为 PART 行（mock 直接返回的子件
        # 即便标了 assembly_id，在 mock 层不会被过滤——SQL 层 `assembly_id IS NULL`
        # 由 repository 集成测试保证；本测只锁定 service 透传）。
        part_items = [i for i in result.items if i.row_type == "PART"]
        assert len(part_items) == 1

        # total = 1 零件（mock 直接返回）+ 1 装配件
        assert result.total == 2

    async def test_serial_no_filter_excludes_assemblies_with_outsource_history(
        self, service, mock_parts, mock_customers, mock_assemblies
    ):
        """has_outsource_history=True 仍然排除装配件段，但 serial_no 仍透传给零件仓储。"""
        cust = _make_customer(id=10, name="ChildCorp")
        mock_customers.get_by_id.return_value = cust
        mock_customers.list_by_ids.return_value = [cust]

        part = _make_part(id=1, customer_id=10, serial_no="L1067-03")
        mock_parts.list_with_filters.return_value = [part]
        mock_parts.count_with_filters.return_value = 1

        mock_assemblies.list_with_filters.return_value = [
            _make_assembly(id=100, customer_id=10, serial_no="L1067")
        ]
        mock_assemblies.count_with_filters.return_value = 1

        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_parts.session.execute.return_value = result_mock

        query = PartListQuery(
            include_assemblies=True,
            serial_no="L1067-03",
            has_outsource_history=True,
        )
        result = await service.list_parts(query)

        # 装配件仓储完全跳过
        mock_assemblies.list_with_filters.assert_not_awaited()
        mock_assemblies.count_with_filters.assert_not_awaited()
        # 仍把 serial_no 透传给零件仓储
        list_kwargs = mock_parts.list_with_filters.await_args.kwargs
        assert list_kwargs["serial_no"] == "L1067-03"
        # 行全部是 PART
        assert all(i.row_type == "PART" for i in result.items)
        assert result.total == 1
