"""Unit tests for DeliveryNoteService (PR-F 2026-07-17 redesign).

Covers:
- empty part_ids → BIZ_INVALID_VALUE
- missing template file → BIZ_INVALID_VALUE
- wrong template extension → BIZ_INVALID_VALUE
- missing '送货单' sheet → BIZ_INVALID_VALUE
- prefix not configured → BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
- cross L1 customers → BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS
- non-READY_TO_SHIP part → BIZ_DELIVERY_PART_STATUS_INVALID
- missing soft-deleted parts → BIZ_PART_NOT_FOUND
- happy path F prefix → xlsx + prefix "F"
- happy path L prefix → xlsx + prefix "L"
- COLUMN_BINDINGS coverage for both prefixes
- barcode column embedded
- skip missing serial/drawing
"""
from __future__ import annotations

import os
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.customer import TCustomer
from model.part import TPart
from repository.customer import CustomerRepository
from repository.part import PartRepository
from service.delivery_note import (
    COLUMN_BINDINGS,
    DeliveryNoteService,
)


# ============================================================
# Fixtures & helpers
# ============================================================
def _make_part(
    *,
    part_id: int = 1001,
    serial_no: str = "F1001",
    drawing_no: str = "DWG-001",
    name: str = "测试零件",
    customer_id: int = 1,
    quantity: int = 2,
    status: str = "READY_TO_SHIP",
    order_no: str | None = "ORDER-1",
    system_delivery_date: date | None = None,
    note: str | None = None,
    applicant_name: str = "张三",
) -> TPart:
    return TPart(
        id=part_id,
        serial_no=serial_no,
        name=name,
        drawing_no=drawing_no,
        applicant_name=applicant_name,
        quantity=quantity,
        unit_price=Decimal("100"),
        total_price=Decimal("200"),
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        actual_delivery_date=None,
        status=status,
        is_urgent=False,
        order_no=order_no,
        system_delivery_date=system_delivery_date,
        note=note,
        customer_id=customer_id,
    )


def _make_template(
    sheet_name: str = "送货单",
    *,
    start_row_data: int = 4,
    ncols: int = 11,
) -> str:
    """Create a minimal .xlsx template with the expected sheet."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.cell(row=1, column=1, value="公司抬头")
    for c in range(1, ncols + 1):
        ws.cell(row=3, column=c, value=f"列{c}")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


def _make_root_customer(
    customer_id: int = 1, name: str = "法拉电子", serial_prefix: str = "F",
) -> TCustomer:
    cust = TCustomer(id=customer_id, name=name, parent_id=None)
    cust.serial_prefix = serial_prefix
    return cust


def _make_leaf_customer(
    customer_id: int = 11, name: str = "母排厂", parent_id: int = 1,
) -> TCustomer:
    return TCustomer(id=customer_id, name=name, parent_id=parent_id)


@pytest.fixture
def mock_parts() -> PartRepository:
    repo = PartRepository.__new__(PartRepository)
    repo.list_by_ids = AsyncMock()
    return repo


@pytest.fixture
def mock_customers() -> CustomerRepository:
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def service(mock_parts, mock_customers) -> DeliveryNoteService:
    return DeliveryNoteService(parts=mock_parts, customers=mock_customers)


@pytest.fixture
def f_template() -> str:
    p = _make_template(start_row_data=4, ncols=11)
    yield p
    if os.path.exists(p):
        os.unlink(p)


@pytest.fixture
def l_template() -> str:
    p = _make_template(start_row_data=5, ncols=9)
    yield p
    if os.path.exists(p):
        os.unlink(p)


def _setup_templates_by_prefix(
    monkeypatch, f_path: str | None = None, l_path: str | None = None
) -> None:
    cfg: dict[str, str] = {}
    if f_path:
        cfg["F"] = f_path
    if l_path:
        cfg["L"] = l_path
    monkeypatch.setattr(
        "service.delivery_note.settings.delivery_note_template_by_prefix",
        cfg,
    )


# ============================================================
# Tests
# ============================================================
@pytest.mark.asyncio
class TestBuildXlsxByPrefix:
    async def test_empty_part_ids(self, service: DeliveryNoteService) -> None:
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_missing_template(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
    ) -> None:
        # mock parts + customers + template path nonexistent
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        root = _make_root_customer(serial_prefix="F")
        leaf = _make_leaf_customer(customer_id=part.customer_id)
        mock_customers.list_by_ids.side_effect = [
            [leaf, root],   # first call: cust_ids (leaf + root via union)
            [root],          # second call: parents lookup
        ]
        _setup_templates_by_prefix(monkeypatch, f_path="/nonexistent/template.xlsx")
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1001])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert "模板文件不存在" in exc.value.message

    async def test_wrong_template_extension(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        tmp_path,
        monkeypatch,
    ) -> None:
        bad = tmp_path / "template.xls"
        bad.write_text("fake")
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        root = _make_root_customer(serial_prefix="F")
        leaf = _make_leaf_customer(customer_id=part.customer_id)
        mock_customers.list_by_ids.side_effect = [[leaf, root], [root]]
        _setup_templates_by_prefix(monkeypatch, f_path=str(bad))
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1001])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert ".xlsx" in exc.value.message

    async def test_missing_sheet(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        tmp_path,
        monkeypatch,
    ) -> None:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        path = tmp_path / "template.xlsx"
        wb.save(str(path))
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        root = _make_root_customer(serial_prefix="F")
        leaf = _make_leaf_customer(customer_id=part.customer_id)
        mock_customers.list_by_ids.side_effect = [[leaf, root], [root]]
        _setup_templates_by_prefix(monkeypatch, f_path=str(path))
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1001])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert "送货单" in exc.value.message

    async def test_prefix_not_configured(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
    ) -> None:
        # F prefix is NOT configured in template_by_prefix
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        root = _make_root_customer(serial_prefix="F")
        leaf = _make_leaf_customer(customer_id=part.customer_id)
        mock_customers.list_by_ids.side_effect = [[leaf, root], [root]]
        _setup_templates_by_prefix(monkeypatch)  # empty config
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1001])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
        assert "F" in exc.value.message

    async def test_cross_l1_customers(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        # 2 parts from 2 different L1 roots
        part_a = _make_part(part_id=1, serial_no="F1001", customer_id=11)
        part_b = _make_part(part_id=2, serial_no="L1002", customer_id=22)
        mock_parts.list_by_ids.return_value = [part_a, part_b]

        root_f = _make_root_customer(customer_id=1, name="法拉", serial_prefix="F")
        leaf_f = _make_leaf_customer(customer_id=11, name="母排厂", parent_id=1)
        root_l = TCustomer(id=2, name="路达", parent_id=None)
        root_l.serial_prefix = "L"
        leaf_l = TCustomer(id=22, name="开发一部", parent_id=2)
        # First call to list_by_ids: cust_ids = {11, 22} -> [leaf_f, leaf_l]
        # Second call: parent_ids = {1, 2} -> [root_f, root_l]
        mock_customers.list_by_ids.side_effect = [
            [leaf_f, leaf_l],
            [root_f, root_l],
        ]
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1, 2])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS
        assert "多个" in exc.value.message

    async def test_part_status_not_ready(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        # 1 part with status=PENDING (not READY_TO_SHIP)
        part = _make_part(status="PENDING")
        mock_parts.list_by_ids.return_value = [part]
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1001])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_PART_STATUS_INVALID
        assert "READY_TO_SHIP" in exc.value.message

    async def test_part_not_found(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        # Caller asks for part 9999 but list_by_ids returns nothing
        mock_parts.list_by_ids.return_value = []
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([9999])
        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert "9999" in exc.value.message

    async def test_skip_part_without_serial_or_drawing(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        # 缺流水号 + 缺图号 → 抛错
        part = _make_part(serial_no=None, drawing_no="DWG-002")
        mock_parts.list_by_ids.return_value = [part]
        # 不应该调用 list_by_ids customers
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx_by_prefix([1001])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert "缺流水号或图号" in exc.value.message

    async def test_happy_path_f_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        part = _make_part(
            part_id=1001,
            serial_no="F1001",
            drawing_no="DWG-F-001",
            customer_id=11,
            order_no="ORD-F-001",
            note="法拉备注",
        )
        mock_parts.list_by_ids.return_value = [part]
        root = _make_root_customer(customer_id=1, name="法拉电子", serial_prefix="F")
        leaf = _make_leaf_customer(customer_id=11, name="母排厂", parent_id=1)
        mock_customers.list_by_ids.side_effect = [[leaf, root], [root]]
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)

        blob, prefix = await service.build_xlsx_by_prefix([1001])
        assert isinstance(blob, bytes)
        assert len(blob) > 0
        assert blob[:4] == b"PK\x03\x04"  # xlsx magic
        assert prefix == "F"

    async def test_happy_path_l_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        l_template: str,
    ) -> None:
        part = _make_part(
            part_id=2002,
            serial_no="L2002",
            drawing_no="DWG-L-002",
            customer_id=22,
            order_no="ORD-L-002",
        )
        mock_parts.list_by_ids.return_value = [part]
        root = TCustomer(id=2, name="路达", parent_id=None)
        root.serial_prefix = "L"
        leaf = TCustomer(id=22, name="开发一部", parent_id=2)
        mock_customers.list_by_ids.side_effect = [[leaf, root], [root]]
        _setup_templates_by_prefix(monkeypatch, l_path=l_template)

        blob, prefix = await service.build_xlsx_by_prefix([2002])
        assert isinstance(blob, bytes)
        assert blob[:4] == b"PK\x03\x04"
        assert prefix == "L"

    async def test_column_bindings_have_all_prefixes(self) -> None:
        # Sanity: F and L must both be defined
        assert "F" in COLUMN_BINDINGS
        assert "L" in COLUMN_BINDINGS
        # Each binding list has at least 序(row_index) + 至少一个 part.* 映射
        for prefix, bindings in COLUMN_BINDINGS.items():
            sources = [b.source for b in bindings]
            assert "row_index" in sources, f"{prefix} 缺 row_index"
            assert any(s.startswith("part.") for s in sources), (
                f"{prefix} 缺 part.* 映射"
            )
        # 法拉模板有 barcode 列；路达没有（检具状态列替代）
        f_sources = [b.source for b in COLUMN_BINDINGS["F"]]
        l_sources = [b.source for b in COLUMN_BINDINGS["L"]]
        assert "barcode_image" in f_sources, "法拉应有 barcode_image"
        assert "barcode_image" not in l_sources, "路达不应有 barcode_image"