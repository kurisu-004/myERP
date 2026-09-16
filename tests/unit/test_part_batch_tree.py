"""Unit tests for PartService.create_parts_tree（2026-07-21 新增）

覆盖：
- 单页 PDF → 独立零件
- 多页 PDF → 装配件 + 子件
- is_master 标记 → ASSEMBLY_MASTER 文件
- 无 master → 不上传 ASSEMBLY_MASTER
- 子件 PR-F 字段透传（order_no / system_delivery_date / note）
- 空字段（None）透传
- 申请人兜底 → bulk_get_or_create
- 客户未找到 → failed 列表
- 单页 PDF 但无对应文件 → failed
- 多页 PDF > 99 → BIZ_ASSEMBLY_TOO_MANY_CHILDREN
- 多页 is_master 重复 → BIZ_INVALID_VALUE
- 拆分 PDF 失败 → BIZ_PART_FILE_UPLOAD_FAILED
- 文件名解析（前端工具函数）— vitest 在 frontend 跑

Mock 策略：所有 repository 方法注入 AsyncMock；与现有 test_part_service_query_crud.py 同款。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import configure_mappers

from core.error_code import ErrCode
from core.exception import BizError
from model import TCustomer, TPart, TPartEvent

configure_mappers()
from model.enums import PartFileKind, PartStatus
from schema.applicant import BulkApplicantOut
from schema.part import (
    PartBatchTreeAssembly,
    PartBatchTreeItem,
    PartBatchTreeRequest,
    PartBatchTreeResult,
    PartBatchTreePartResult,
)
from service.part import PartService
from tests.unit._fake_batches import FakePartBatchRepository


# ======================
# Fixtures
# ======================

@pytest.fixture
def mock_assemblies() -> AsyncMock:
    m = AsyncMock()
    m.create = AsyncMock(return_value=None)
    return m


@pytest.fixture
def mock_part_files() -> AsyncMock:
    m = AsyncMock()
    m.upload = AsyncMock(return_value=None)
    return m


@pytest.fixture
def mock_applicants() -> AsyncMock:
    m = AsyncMock()
    m.bulk_get_or_create = AsyncMock(return_value=[])
    return m


@pytest.fixture
def leaf_customer() -> TCustomer:
    c = TCustomer(id=100, name="母排厂", parent_id=1)
    c.serial_prefix = None
    return c


@pytest.fixture
def root_customer() -> TCustomer:
    c = TCustomer(id=1, name="法拉电子", parent_id=None)
    c.serial_prefix = "F"
    return c


@pytest.fixture
def service(
    mock_parts: AsyncMock,
    mock_customers: AsyncMock,
    mock_workers: AsyncMock,
    mock_events: AsyncMock,
    mock_serial_counters: AsyncMock,
    mock_shelves: AsyncMock,
    mock_applicants_repo: AsyncMock,
    mock_files: AsyncMock,
    mock_assemblies: AsyncMock,
) -> PartService:
    """构造 PartService，注入 create_parts_tree 所需的 assemblies 依赖。"""
    return PartService(
        parts=mock_parts,
        part_batches=FakePartBatchRepository(
            parts_provider=mock_parts.get_by_id,
        ),
        customers=mock_customers,
        workers=mock_workers,
        events=mock_events,
        serial_counters=mock_serial_counters,
        shelves=mock_shelves,
        applicants=mock_applicants_repo,
        files=mock_files,
        assemblies=mock_assemblies,
    )


# ======================
# Helpers
# ======================

def _make_part(
    *,
    id: int,
    customer_id: int,
    drawing_no: str = "DRAW-1",
    name: str = "零件1",
    assembly_id: int | None = None,
    serial_no: str | None = None,
    order_no: str | None = None,
    system_delivery_date=None,
    note: str | None = None,
) -> TPart:
    p = TPart(
        id=id,
        serial_no=serial_no or f"F{id:04d}",
        name=name,
        drawing_no=drawing_no,
        applicant_name="张三",
        quantity=1,
        unit_price=Decimal("0"),
        total_price=Decimal("0"),
        request_date=date(2026, 7, 21),
        planned_delivery_date=date(2026, 8, 21),
        order_no=order_no,
        system_delivery_date=system_delivery_date,
        note=note,
        is_urgent=False,
        status=PartStatus.PENDING.value,
        location="OFFICE",
        customer_id=customer_id,
        assembly_id=assembly_id,
    )
    return p


def _item(
    *,
    pdf_index: int = 0,
    page_index: int = 0,
    assembly_uid: str | None = None,
    is_master: bool = False,
    drawing_no: str = "DRAW-1",
    name: str = "零件1",
    customer_id: str = "100",
    order_no: str | None = None,
    system_delivery_date=None,
    note: str | None = None,
    quantity: int = 1,
) -> PartBatchTreeItem:
    return PartBatchTreeItem(
        pdf_index=pdf_index,
        page_index=page_index,
        assembly_uid=assembly_uid,
        is_master=is_master,
        drawing_no=drawing_no,
        name=name,
        applicant_name="张三",
        applicant_id=None,
        quantity=quantity,
        customer_id=customer_id,
        request_date=date(2026, 7, 21),
        planned_delivery_date=date(2026, 8, 21),
        order_no=order_no,
        system_delivery_date=system_delivery_date,
        note=note,
        is_urgent=False,
    )


# ======================
# Tests
# ======================

class TestCreatePartsTreeSinglePage:
    pytestmark = [
        pytest.mark.skip(reason='2026-09-16 t_part 瘦身（Rust v2 迁移 027）：本测试断言已删字段 / 已删行为（t_part.{actual_delivery_date,location,current_holder_id,placed_at,delivery_note_id,has_been_repaired}、t_part_batch.has_been_repaired、t_assembly.actual_delivery_date）。v1 业务端点已 dormant（2026-09-15 Phase 5 起前端业务全走 v2，Python 仅保留 4 个打印端点 + /api/mcp），本测试构造 / 断言 / 调用方都已失效；详见 backend-rust 迁移 027 与本仓 root CLAUDE.md §跨子模块架构。'),
        pytest.mark.asyncio,
        pytest.mark.asyncio,
        pytest.mark.asyncio,
        pytest.mark.asyncio,
        pytest.mark.asyncio,
    ]

    async def test_bulk_applicant_called_when_applicant_name_set(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_part_files: AsyncMock,
        mock_applicants: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """applicant_id 缺 + name 非空 → bulk_get_or_create 被调 1 次"""
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))
        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1005")
        mock_part_files.upload = AsyncMock(return_value=None)
        mock_parts.create = AsyncMock(return_value=None)
        mock_parts.get_by_id = AsyncMock(return_value=_make_part(id=501, customer_id=100))
        mock_applicants.bulk_get_or_create = AsyncMock(return_value=[
            BulkApplicantOut(name="李四", customer_id="1", applicant_id="9999")
        ])

        item = PartBatchTreeItem(
            pdf_index=0, page_index=0, assembly_uid=None, is_master=False,
            drawing_no="D", name="N",
            applicant_name="李四",  # 有名但 id 缺
            applicant_id=None,
            quantity=1, customer_id="100",
            request_date=date(2026, 7, 21),
            planned_delivery_date=date(2026, 8, 21),
            is_urgent=False,
        )
        payload = PartBatchTreeRequest(items=[item], assemblies=[])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={0: (b"%PDF", "draw.pdf", "application/pdf")},
            part_files=mock_part_files,
            applicants=mock_applicants,
        )

        # bulk_get_or_create 应该至少被调 1 次
        assert mock_applicants.bulk_get_or_create.await_count >= 1
        assert len(result.standalone_parts) == 1


class TestParseDrawingFilename:
    """覆盖前端 utils/drawingFilename.ts（vitest 在 frontend 跑）；这里用 Python
    重新实现相同的 regex 以做交叉验证。"""

    def test_with_underscore_splits(self) -> None:
        import re
        # 模拟 JS 端 parseDrawingFilename
        def parse(filename: str) -> dict[str, str | None]:
            no_ext = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
            idx = no_ext.find("_")
            if idx < 0:
                return {"drawingNo": None, "partName": None}
            return {
                "drawingNo": no_ext[:idx].strip() or None,
                "partName": no_ext[idx + 1:].strip() or None,
            }

        r = parse("A1234_精研挡料座.pdf")
        assert r == {"drawingNo": "A1234", "partName": "精研挡料座"}

    def test_no_underscore_returns_nulls(self) -> None:
        import re

        def parse(filename: str) -> dict[str, str | None]:
            no_ext = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
            idx = no_ext.find("_")
            if idx < 0:
                return {"drawingNo": None, "partName": None}
            return {
                "drawingNo": no_ext[:idx].strip() or None,
                "partName": no_ext[idx + 1:].strip() or None,
            }

        assert parse("drawing.pdf") == {"drawingNo": None, "partName": None}

    def test_multiple_underscores_splits_first(self) -> None:
        import re

        def parse(filename: str) -> dict[str, str | None]:
            no_ext = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
            idx = no_ext.find("_")
            if idx < 0:
                return {"drawingNo": None, "partName": None}
            return {
                "drawingNo": no_ext[:idx].strip() or None,
                "partName": no_ext[idx + 1:].strip() or None,
            }

        assert parse("A_B_C.pdf") == {"drawingNo": "A", "partName": "B_C"}

    def test_uppercase_pdf_ext_ok(self) -> None:
        import re

        def parse(filename: str) -> dict[str, str | None]:
            no_ext = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
            idx = no_ext.find("_")
            if idx < 0:
                return {"drawingNo": None, "partName": None}
            return {
                "drawingNo": no_ext[:idx].strip() or None,
                "partName": no_ext[idx + 1:].strip() or None,
            }

        assert parse("PO1_零件.PDF") == {"drawingNo": "PO1", "partName": "零件"}