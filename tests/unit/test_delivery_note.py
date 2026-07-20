"""Unit tests for DeliveryNoteService (PR-F 2026-07-17 redesign; 2026-07-20 新模板).

Covers:
- empty part_ids → BIZ_INVALID_VALUE
- missing template file → BIZ_INVALID_VALUE
- wrong template extension → BIZ_INVALID_VALUE
- missing TEMPLATE_CONFIGS[prefix].sheet_name → BIZ_INVALID_VALUE
- prefix not configured → BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
- cross L1 customers → BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS
- non-READY_TO_SHIP part → BIZ_DELIVERY_PART_STATUS_INVALID
- missing soft-deleted parts → BIZ_PART_NOT_FOUND
- happy path F prefix → xlsx + prefix "F"
- happy path L prefix → xlsx + prefix "L"
- TEMPLATE_CONFIGS coverage for both prefixes
- explicit template == auto-derived prefix → same path
- explicit template mismatches root prefix → BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
- overflow F (15 parts > max_rows=14) → BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
- overflow L (26 parts > max_rows=25) → BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
- Luda data writes start at R5 (does not overwrite R3-R4 two-row header)
- Luda sheet name "杏南" / Fala sheet name "Sheet1" are respected
- 49 Fala template images survive load+save round-trip
- Pydantic request schema accepts omitted template (backward compat)
"""
from __future__ import annotations

import io
import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import status as http_status
from openpyxl import load_workbook

from api.v1.delivery_note import DeliveryNoteGenerateRequest
from core.config import settings as real_settings
from core.error_code import ErrCode
from core.exception import BizError
from model.customer import TCustomer
from model.part import TPart
from repository.customer import CustomerRepository
from repository.part import PartRepository
from service.delivery_note import (
    TEMPLATE_CONFIGS,
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
    *,
    sheet_name: str | None = None,
    start_row: int | None = None,
    ncols: int | None = None,
    prefix: str = "F",
) -> str:
    """Create a minimal .xlsx template matching `TEMPLATE_CONFIGS[prefix]`."""
    cfg = TEMPLATE_CONFIGS[prefix]
    sheet_name = sheet_name or cfg.sheet_name
    start_row = start_row or cfg.start_row
    ncols = ncols or max(b.col for b in cfg.bindings)

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    # 写一行占位列头（start_row - 1），方便断言「数据不覆盖列头」
    header_row = start_row - 1
    if header_row >= 1:
        for c in range(1, ncols + 1):
            ws.cell(row=header_row, column=c, value=f"列{c}")
    # 在 data 区预填一行占位（用于断言「数据从 start_row 开始覆盖」）
    for c in range(1, ncols + 1):
        ws.cell(row=start_row, column=c, value="PLACEHOLDER")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


def _make_template_with_fala_images() -> str:
    """复制真实的法模板（带 49 张嵌入图）到一个 tmp 路径用于 round-trip 测试。"""
    src = Path(real_settings.delivery_note_template_by_prefix["F"])
    if not src.is_absolute():
        # 相对进程 CWD；pytest 一般从项目根目录跑
        src = Path.cwd() / src
    fd, dst = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(fin.read())
    return dst


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
    p = _make_template(prefix="F")
    yield p
    if os.path.exists(p):
        os.unlink(p)


@pytest.fixture
def l_template() -> str:
    p = _make_template(prefix="L")
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


def _mock_root_and_leaf(
    mock_customers: CustomerRepository,
    *,
    part_customer_id: int,
    root_id: int = 1,
    root_name: str = "法拉电子",
    root_prefix: str = "F",
    leaf_name: str = "母排厂",
) -> None:
    """设置 mock_customers.list_by_ids 让其分别返回 leaf+root 和仅 root。"""
    root = _make_root_customer(
        customer_id=root_id, name=root_name, serial_prefix=root_prefix,
    )
    leaf = _make_leaf_customer(
        customer_id=part_customer_id, name=leaf_name, parent_id=root_id,
    )
    mock_customers.list_by_ids.side_effect = [
        [leaf, root],   # first call: cust_ids
        [root],         # second call: parent_ids
    ]


# ============================================================
# Tests
# ============================================================
@pytest.mark.asyncio
class TestBuildXlsx:
    async def test_empty_part_ids(self, service: DeliveryNoteService) -> None:
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == http_status.HTTP_400_BAD_REQUEST

    async def test_missing_template(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
    ) -> None:
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        _setup_templates_by_prefix(monkeypatch, f_path="/nonexistent/template.xlsx")
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001])
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
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        _setup_templates_by_prefix(monkeypatch, f_path=str(bad))
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001])
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
        ws.title = "WrongSheet"  # 不是 F 模板期望的 Sheet1
        path = tmp_path / "template.xlsx"
        wb.save(str(path))
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        _setup_templates_by_prefix(monkeypatch, f_path=str(path))
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001])
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        expected_sheet = TEMPLATE_CONFIGS["F"].sheet_name
        assert expected_sheet in exc.value.message

    async def test_prefix_not_configured(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
    ) -> None:
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        _setup_templates_by_prefix(monkeypatch)  # empty config
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
        assert "F" in exc.value.message

    async def test_template_config_missing_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
    ) -> None:
        # settings 有 F 的路径，但 TEMPLATE_CONFIGS 没有 F → BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
        part = _make_part()
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        # 用一个真实存在的 fake xlsx 绕开 path-not-found
        fd, fake = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            _setup_templates_by_prefix(monkeypatch, f_path=fake)
            # monkeypatch TEMPLATE_CONFIGS 临时删除 F
            monkeypatch.setitem(TEMPLATE_CONFIGS, "F", None)  # type: ignore[arg-type]
            with pytest.raises(BizError) as exc:
                await service.build_xlsx([1001])
            assert exc.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
        finally:
            if os.path.exists(fake):
                os.unlink(fake)

    async def test_cross_l1_customers(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        part_a = _make_part(part_id=1, serial_no="F1001", customer_id=11)
        part_b = _make_part(part_id=2, serial_no="L1002", customer_id=22)
        mock_parts.list_by_ids.return_value = [part_a, part_b]

        root_f = _make_root_customer(customer_id=1, name="法拉", serial_prefix="F")
        leaf_f = _make_leaf_customer(customer_id=11, name="母排厂", parent_id=1)
        root_l = TCustomer(id=2, name="路达", parent_id=None)
        root_l.serial_prefix = "L"
        leaf_l = TCustomer(id=22, name="开发一部", parent_id=2)
        mock_customers.list_by_ids.side_effect = [
            [leaf_f, leaf_l],
            [root_f, root_l],
        ]
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1, 2])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS
        assert "多个" in exc.value.message

    async def test_part_status_not_ready(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        part = _make_part(status="PENDING")
        mock_parts.list_by_ids.return_value = [part]
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_PART_STATUS_INVALID
        assert "READY_TO_SHIP" in exc.value.message

    async def test_part_not_found(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        mock_parts.list_by_ids.return_value = []
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([9999])
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
        part = _make_part(serial_no=None, drawing_no="DWG-002")
        mock_parts.list_by_ids.return_value = [part]
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)
        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001])
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
        _mock_root_and_leaf(
            mock_customers,
            part_customer_id=part.customer_id,
            root_name="法拉电子",
            root_prefix="F",
            leaf_name="母排厂",
        )
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)

        blob, prefix = await service.build_xlsx([1001])
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
        # 路达 root: serial_prefix=L
        _mock_root_and_leaf(
            mock_customers,
            part_customer_id=part.customer_id,
            root_id=2,
            root_name="路达",
            root_prefix="L",
            leaf_name="开发一部",
        )
        _setup_templates_by_prefix(monkeypatch, l_path=l_template)

        blob, prefix = await service.build_xlsx([2002])
        assert isinstance(blob, bytes)
        assert blob[:4] == b"PK\x03\x04"
        assert prefix == "L"

    async def test_template_configs_have_all_prefixes(self) -> None:
        # Sanity: F and L must both be defined in TEMPLATE_CONFIGS
        assert "F" in TEMPLATE_CONFIGS
        assert "L" in TEMPLATE_CONFIGS
        for prefix, cfg in TEMPLATE_CONFIGS.items():
            sources = [b.source for b in cfg.bindings]
            assert "row_index" in sources, f"{prefix} 缺 row_index"
            assert any(s.startswith("part.") for s in sources), (
                f"{prefix} 缺 part.* 映射"
            )
            assert cfg.start_row >= 3, f"{prefix} start_row 太靠上"
            assert cfg.max_rows >= 1, f"{prefix} max_rows 必须为正"
            assert cfg.sheet_name, f"{prefix} sheet_name 必填"
        # 新模板 max_rows：法 14 / 路 25
        assert TEMPLATE_CONFIGS["F"].max_rows == 14
        assert TEMPLATE_CONFIGS["F"].start_row == 3
        assert TEMPLATE_CONFIGS["F"].sheet_name == "Sheet1"
        assert TEMPLATE_CONFIGS["L"].max_rows == 25
        assert TEMPLATE_CONFIGS["L"].start_row == 5
        assert TEMPLATE_CONFIGS["L"].sheet_name == "杏南"

    # ------------------------------------------------------------------
    # 新增用例（2026-07-20）
    # ------------------------------------------------------------------
    async def test_image_preservation_f_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
    ) -> None:
        """真实法模板（49 张图）经 load+save 后图片不丢。"""
        fala_template = _make_template_with_fala_images()
        try:
            part = _make_part(
                part_id=1001,
                serial_no="F1001",
                drawing_no="DWG-F-001",
                customer_id=11,
            )
            mock_parts.list_by_ids.return_value = [part]
            _mock_root_and_leaf(
                mock_customers, part_customer_id=part.customer_id,
            )
            _setup_templates_by_prefix(monkeypatch, f_path=fala_template)

            blob, prefix = await service.build_xlsx([1001])
            assert prefix == "F"

            # 重新打开生成的 blob，断言 F 模板的 49 张图全在
            wb_out = load_workbook(io.BytesIO(blob))
            ws_out = wb_out["Sheet1"]
            assert len(ws_out._images) == 49, (
                f"法模板图片丢失：期望 49，实际 {len(ws_out._images)}"
            )
        finally:
            if os.path.exists(fala_template):
                os.unlink(fala_template)

    async def test_explicit_template_matches_root_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        """显式 template 与 auto-derived prefix 一致 → 走相同路径，blob 正常。"""
        part = _make_part(part_id=1001, customer_id=11)
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)

        blob, prefix = await service.build_xlsx([1001], template="F")
        assert isinstance(blob, bytes)
        assert blob[:4] == b"PK\x03\x04"
        assert prefix == "F"

    async def test_explicit_template_mismatch_root_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        l_template: str,
    ) -> None:
        """法拉零件显式传 template="L" → 防呆拒绝。"""
        part = _make_part(
            part_id=1001, customer_id=11, serial_no="F1001", drawing_no="D-F",
        )
        mock_parts.list_by_ids.return_value = [part]
        # 零件所属 root 是 F
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id, root_prefix="F",
        )
        # 但 settings 只配了 L 路径（避开 template-not-found）
        _setup_templates_by_prefix(monkeypatch, l_path=l_template)

        with pytest.raises(BizError) as exc:
            await service.build_xlsx([1001], template="L")
        assert exc.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
        assert "L" in exc.value.message and "F" in exc.value.message

    async def test_overflow_too_many_parts_f_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        """15 件法拉零件（> max_rows=14）→ BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS。"""
        parts = [
            _make_part(
                part_id=1000 + i,
                serial_no=f"F{1000+i}",
                drawing_no=f"D-F-{i:03d}",
                customer_id=11,
            )
            for i in range(15)
        ]
        mock_parts.list_by_ids.return_value = parts
        _mock_root_and_leaf(
            mock_customers, part_customer_id=11, root_prefix="F",
        )
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)

        with pytest.raises(BizError) as exc:
            await service.build_xlsx([p.id for p in parts])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
        assert "14" in exc.value.message
        assert "15" in exc.value.message

    async def test_overflow_too_many_parts_l_prefix(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        l_template: str,
    ) -> None:
        """26 件路达零件（> max_rows=25）→ BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS。"""
        parts = [
            _make_part(
                part_id=2000 + i,
                serial_no=f"L{2000+i}",
                drawing_no=f"D-L-{i:03d}",
                customer_id=22,
            )
            for i in range(26)
        ]
        mock_parts.list_by_ids.return_value = parts
        _mock_root_and_leaf(
            mock_customers,
            part_customer_id=22,
            root_id=2,
            root_name="路达",
            root_prefix="L",
            leaf_name="开发一部",
        )
        _setup_templates_by_prefix(monkeypatch, l_path=l_template)

        with pytest.raises(BizError) as exc:
            await service.build_xlsx([p.id for p in parts])
        assert exc.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
        assert "25" in exc.value.message
        assert "26" in exc.value.message

    async def test_luda_data_starts_at_row5(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        l_template: str,
    ) -> None:
        """路达模板 R5 写入「序号=1」，R4 列头保持占位 PLACEHOLDER。"""
        part = _make_part(
            part_id=2002,
            serial_no="L2002",
            drawing_no="DWG-L-002",
            customer_id=22,
        )
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers,
            part_customer_id=22,
            root_id=2,
            root_name="路达",
            root_prefix="L",
            leaf_name="开发一部",
        )
        _setup_templates_by_prefix(monkeypatch, l_path=l_template)

        blob, _prefix = await service.build_xlsx([2002])
        wb = load_workbook(io.BytesIO(blob))
        ws = wb["杏南"]
        cfg = TEMPLATE_CONFIGS["L"]
        # 数据从 R5 起：第 1 列 = 1（行号）
        assert ws.cell(row=cfg.start_row, column=1).value == 1
        # R3-R4 表头行第 1 列仍为占位（_make_template 写了 start_row-1 行）
        header_row = cfg.start_row - 1
        assert ws.cell(row=header_row, column=1).value == "列1"

    async def test_barcode_skipped_for_luda(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        l_template: str,
    ) -> None:
        """路达模板 cfg.barcode_col is None，生成结果不附加任何 image。"""
        part = _make_part(
            part_id=2002,
            serial_no="L2002",
            drawing_no="DWG-L-002",
            customer_id=22,
        )
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers,
            part_customer_id=22,
            root_id=2,
            root_name="路达",
            root_prefix="L",
            leaf_name="开发一部",
        )
        _setup_templates_by_prefix(monkeypatch, l_path=l_template)

        blob, _prefix = await service.build_xlsx([2002])
        wb = load_workbook(io.BytesIO(blob))
        ws = wb["杏南"]
        assert len(ws._images) == 0


# ============================================================
# Pydantic request schema tests（向后兼容）
# ============================================================
class TestDeliveryNoteGenerateRequest:
    def test_omitted_template(self) -> None:
        """不带 template 字段 → 通过校验（旧调用方兼容）。"""
        req = DeliveryNoteGenerateRequest(part_ids=["1001", "1002"])
        assert req.template is None
        assert req.part_ids == ["1001", "1002"]

    def test_explicit_template_f(self) -> None:
        req = DeliveryNoteGenerateRequest(part_ids=["1001"], template="F")
        assert req.template == "F"

    def test_explicit_template_l(self) -> None:
        req = DeliveryNoteGenerateRequest(part_ids=["1001"], template="L")
        assert req.template == "L"

    def test_invalid_template_rejected(self) -> None:
        """template 只能是 F / L / None，其他值 Pydantic 拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DeliveryNoteGenerateRequest(part_ids=["1001"], template="X")  # type: ignore[arg-type]

    def test_empty_part_ids_rejected(self) -> None:
        """min_length=1 保护。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DeliveryNoteGenerateRequest(part_ids=[])


# ============================================================
# 向后兼容：旧 API 别名
# ============================================================
@pytest.mark.asyncio
class TestBuildXlsxByPrefixAlias:
    async def test_alias_calls_build_xlsx_with_template_none(
        self,
        service: DeliveryNoteService,
        mock_parts: PartRepository,
        mock_customers: CustomerRepository,
        monkeypatch,
        f_template: str,
    ) -> None:
        """`build_xlsx_by_prefix` 是旧入口；等价于 `build_xlsx(part_ids)`。"""
        part = _make_part(part_id=1001, customer_id=11)
        mock_parts.list_by_ids.return_value = [part]
        _mock_root_and_leaf(
            mock_customers, part_customer_id=part.customer_id,
        )
        _setup_templates_by_prefix(monkeypatch, f_path=f_template)

        blob, prefix = await service.build_xlsx_by_prefix([1001])
        assert isinstance(blob, bytes)
        assert prefix == "F"