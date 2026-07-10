"""Unit tests for DeliveryNoteService (PR-B 2026-07-10).

Covers:
- empty part_ids → BIZ_INVALID_VALUE
- missing template file → BIZ_INVALID_VALUE
- wrong template extension → BIZ_INVALID_VALUE
- missing '送货单' sheet → BIZ_INVALID_VALUE
- happy path with mocked PartRepository + CustomerRepository + a real .xlsx
  template generated on the fly by openpyxl
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
from model.part import TPart
from repository.customer import CustomerRepository
from repository.part import PartRepository
from service.delivery_note import DeliveryNoteService


def _make_part(
    *,
    part_id: int = 1001,
    serial_no: str = "L1001",
    drawing_no: str = "DWG-001",
    name: str = "测试零件",
    customer_id: int = 1,
    quantity: int = 2,
) -> TPart:
    part = TPart(
        id=part_id,
        serial_no=serial_no,
        name=name,
        drawing_no=drawing_no,
        applicant_name="张三",
        quantity=quantity,
        unit_price=Decimal("100"),
        total_price=Decimal("200"),
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        status="READY_TO_SHIP",
        is_urgent=False,
        customer_id=customer_id,
    )
    return part


def _make_minimal_template(sheet_name: str = "送货单") -> str:
    """Create a minimal .xlsx template with the expected sheet and write to tempdir."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.cell(row=1, column=1, value="公司抬头（洪升宏）")
    ws.cell(row=3, column=1, value="序号")
    ws.cell(row=3, column=2, value="流水号")
    ws.cell(row=3, column=3, value="图号")
    ws.cell(row=3, column=4, value="名称")
    ws.cell(row=3, column=5, value="申请人")
    ws.cell(row=3, column=6, value="客户")
    ws.cell(row=3, column=7, value="数量")
    ws.cell(row=3, column=8, value="计划交期")
    ws.cell(row=3, column=9, value="条码图")

    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


@pytest.fixture
def mock_parts() -> PartRepository:
    repo = PartRepository.__new__(PartRepository)
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def mock_customers() -> CustomerRepository:
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def service(mock_parts, mock_customers) -> DeliveryNoteService:
    return DeliveryNoteService(parts=mock_parts, customers=mock_customers)


@pytest.mark.asyncio
class TestBuildDeliveryNoteXlsx:
    async def test_empty_part_ids(
        self, service: DeliveryNoteService,
    ) -> None:
        with pytest.raises(BizError) as exc:
            await service.build_delivery_note_xlsx([])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_missing_template(
        self,
        service: DeliveryNoteService,
        monkeypatch,
    ) -> None:
        # 路径指向不存在的文件
        monkeypatch.setattr(
            "service.delivery_note.settings.delivery_note_template_path",
            "/nonexistent/path/template.xlsx",
        )
        with pytest.raises(BizError) as exc:
            await service.build_delivery_note_xlsx([1])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert "模板文件不存在" in exc.value.message

    async def test_wrong_template_extension(
        self, service: DeliveryNoteService, tmp_path, monkeypatch,
    ) -> None:
        bad = tmp_path / "template.xls"
        bad.write_text("fake")
        monkeypatch.setattr(
            "service.delivery_note.settings.delivery_note_template_path",
            str(bad),
        )
        with pytest.raises(BizError) as exc:
            await service.build_delivery_note_xlsx([1])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert ".xlsx" in exc.value.message

    async def test_missing_sheet(
        self, service: DeliveryNoteService, tmp_path, monkeypatch,
    ) -> None:
        # 模板里只有 Sheet1，没有「送货单」
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        path = tmp_path / "template.xlsx"
        wb.save(str(path))

        monkeypatch.setattr(
            "service.delivery_note.settings.delivery_note_template_path",
            str(path),
        )
        with pytest.raises(BizError) as exc:
            await service.build_delivery_note_xlsx([1])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert "送货单" in exc.value.message

    async def test_happy_path(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        monkeypatch,
    ) -> None:
        template_path = _make_minimal_template("送货单")
        try:
            monkeypatch.setattr(
                "service.delivery_note.settings.delivery_note_template_path",
                template_path,
            )
            mock_parts.get_by_id.return_value = _make_part()

            blob = await service.build_delivery_note_xlsx([1001])
            assert isinstance(blob, bytes)
            assert len(blob) > 0
            # 验证是 .xlsx（PK\x03\x04 = zip magic）
            assert blob[:4] == b"PK\x03\x04"
        finally:
            if os.path.exists(template_path):
                os.unlink(template_path)

    async def test_skip_part_without_serial_or_drawing(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        monkeypatch,
    ) -> None:
        template_path = _make_minimal_template("送货单")
        try:
            monkeypatch.setattr(
                "service.delivery_note.settings.delivery_note_template_path",
                template_path,
            )
            # 缺流水的零件 + 缺图号的零件（两个都不合格）
            bad_part = _make_part(
                part_id=2002, serial_no=None, drawing_no="DWG-002",
            )
            bad_part.serial_no = None
            mock_parts.get_by_id.return_value = bad_part

            with pytest.raises(BizError) as exc:
                await service.build_delivery_note_xlsx([2002])
            assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
            assert "缺流水号或图号" in exc.value.message
        finally:
            if os.path.exists(template_path):
                os.unlink(template_path)