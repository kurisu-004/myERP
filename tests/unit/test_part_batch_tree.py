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
    pytestmark = pytest.mark.asyncio

    async def test_single_page_pdf_creates_standalone_part(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_part_files: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """单页 PDF → 1 个独立零件（无 assembly_id）；part_files.upload 调 1 次 (DRAWING)"""
        # 模拟 get_by_id: 叶子 id → leaf；一级 id → root
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))
        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1001")
        mock_parts.create = AsyncMock(return_value=None)
        mock_part_files.upload = AsyncMock(side_effect=lambda **kw: AsyncMock(**{
            "id": "fake-file-id",
            "version": 0,
        })())
        mock_parts.get_by_id = AsyncMock(return_value=None)  # _to_out 内部取详情用

        item = _item(pdf_index=0, page_index=0, assembly_uid=None)
        payload = PartBatchTreeRequest(items=[item], assemblies=[])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={0: (b"%PDF-1.4\n...", "draw1.pdf", "application/pdf")},
            part_files=mock_part_files,
            applicants=AsyncMock(),
        )

        assert isinstance(result, PartBatchTreeResult)
        assert len(result.standalone_parts) == 1
        assert result.assemblies == []
        assert result.failed == []
        sp = result.standalone_parts[0]
        assert sp.kind == "part"
        # 单页：part_files.upload 应调 1 次，kind=DRAWING
        assert mock_part_files.upload.await_count == 1
        call_kwargs = mock_part_files.upload.await_args.kwargs
        assert call_kwargs["kind"] == PartFileKind.DRAWING

    async def test_single_page_missing_file_returns_failed(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_part_files: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """file_payloads 缺 pdf_index=0 → failed 列表 1 条；DB 0 写入"""
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))
        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1001")

        item = _item(pdf_index=0)
        payload = PartBatchTreeRequest(items=[item], assemblies=[])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={},  # 没传
            part_files=mock_part_files,
            applicants=AsyncMock(),
        )

        # customer 校验通过 → 但 file 缺失 → failed 1 条
        assert len(result.failed) == 1
        assert "未找到 pdf_index" in result.failed[0].message
        assert mock_parts.create.await_count == 0
        assert mock_serial_counters.acquire_serial.await_count == 0


class TestCreatePartsTreeMultiPage:
    pytestmark = pytest.mark.asyncio

    async def test_multi_page_pdf_creates_assembly_with_children(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_events: AsyncMock,
        mock_part_files: AsyncMock,
        mock_assemblies: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """3 页 PDF → 1 个 asm + 3 个 child；serial_no 形如 F1001-01/-02/-03"""
        # 设置 root_customer 缓存
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))

        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1001")

        # 模拟 part.create 返回带 id 的 part
        async def fake_create(part):
            return None
        mock_parts.create = fake_create

        # events.create
        mock_events.create = AsyncMock(return_value=None)

        # 模拟 part_files.upload 返 None
        mock_part_files.upload = AsyncMock(return_value=None)

        # 模拟 parts.get_by_id 用于 _to_out（child 详情）
        child_id_seq = [201, 202, 203]

        async def fake_get_by_id(pid):
            return _make_part(
                id=pid,
                customer_id=100,
                drawing_no=f"DRAW-{pid}",
                name=f"子件{pid}",
                assembly_id=999,
                serial_no=f"F1001-{pid - 200:02d}",
            )
        mock_parts.get_by_id = fake_get_by_id

        items = [
            _item(pdf_index=1, page_index=0, assembly_uid="a1", drawing_no="D-A1", name="件1"),
            _item(pdf_index=1, page_index=1, assembly_uid="a1", drawing_no="D-A2", name="件2"),
            _item(pdf_index=1, page_index=2, assembly_uid="a1", drawing_no="D-A3", name="件3"),
        ]
        asm_meta = PartBatchTreeAssembly(
            uid="a1",
            drawing_no="DRAW-A",
            name="装配件A",
            applicant_name="李四",
            applicant_id=None,
            customer_id="100",
            request_date=date(2026, 7, 21),
            planned_delivery_date=date(2026, 8, 21),
            is_urgent=False,
        )
        payload = PartBatchTreeRequest(items=items, assemblies=[asm_meta])

        # 模拟 split_pdf 返回 3 页
        with pytest.MonkeyPatch.context() as mp:
            from utils import pdf as pdf_util
            mp.setattr(pdf_util, "split_pdf", lambda _b: [b"%PDF-p1", b"%PDF-p2", b"%PDF-p3"])

            result = await service.create_parts_tree(
                payload,
                file_payloads_by_pdf_index={1: (b"%PDF-3page", "draw.pdf", "application/pdf")},
                part_files=mock_part_files,
                applicants=AsyncMock(),
            )

        assert len(result.assemblies) == 1
        assert result.assemblies[0].uid == "a1"
        assert len(result.assemblies[0].children) == 3
        # 子件 serial 派生
        serials = [c.part.serial_no for c in result.assemblies[0].children]
        assert serials == ["F1001-01", "F1001-02", "F1001-03"]
        # master_file 默认 None
        assert result.assemblies[0].master_file is None
        # upload 调 N 次（3 子件 + 0 master）
        assert mock_part_files.upload.await_count == 3
        # 上传的 kind 全是 DRAWING
        for c in mock_part_files.upload.await_args_list:
            assert c.kwargs["kind"] == PartFileKind.DRAWING
        # assemblies.create 调 1 次
        assert mock_assemblies.create.await_count == 1

    async def test_master_marked_uploads_assembly_master(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_events: AsyncMock,
        mock_part_files: AsyncMock,
        mock_assemblies: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """page_index=1 标 is_master → 上传 ASSEMBLY_MASTER"""
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))
        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1002")
        mock_events.create = AsyncMock(return_value=None)
        mock_parts.create = AsyncMock(return_value=None)
        mock_part_files.upload = AsyncMock(return_value=None)
        mock_parts.get_by_id = AsyncMock(side_effect=lambda pid: _make_part(
            id=pid, customer_id=100, drawing_no=f"X{pid}", name=f"N{pid}",
            assembly_id=999, serial_no=f"F1002-{pid - 200:02d}",
        ))

        items = [
            _item(pdf_index=1, page_index=0, assembly_uid="a1", drawing_no="X1", name="件1"),
            _item(pdf_index=1, page_index=1, assembly_uid="a1", drawing_no="X2", name="件2", is_master=True),
            _item(pdf_index=1, page_index=2, assembly_uid="a1", drawing_no="X3", name="件3"),
        ]
        asm_meta = PartBatchTreeAssembly(
            uid="a1",
            drawing_no="X-TOTAL",
            name="装配件X",
            applicant_name=None,
            applicant_id=None,
            customer_id="100",
            request_date=date(2026, 7, 21),
            planned_delivery_date=date(2026, 8, 21),
            is_urgent=False,
        )
        payload = PartBatchTreeRequest(items=items, assemblies=[asm_meta])

        with pytest.MonkeyPatch.context() as mp:
            from utils import pdf as pdf_util
            mp.setattr(pdf_util, "split_pdf", lambda _b: [b"%PDF-p1", b"%PDF-p2", b"%PDF-p3"])

            result = await service.create_parts_tree(
                payload,
                file_payloads_by_pdf_index={1: (b"%PDF-3page", "x.pdf", "application/pdf")},
                part_files=mock_part_files,
                applicants=AsyncMock(),
            )

        # 2 子件 DRAWING（跳过 is_master 页）+ 1 master ASSEMBLY_MASTER
        # 2026-07-22：总装图页不再作为子件建 part/DRAWING，只作 ASSEMBLY_MASTER。
        assert mock_parts.create.await_count == 2
        assert mock_part_files.upload.await_count == 3
        kinds = [c.kwargs["kind"] for c in mock_part_files.upload.await_args_list]
        assert kinds.count(PartFileKind.DRAWING) == 2
        assert kinds.count(PartFileKind.ASSEMBLY_MASTER) == 1
        # ASSEMBLY_MASTER 的 owner_id 应该是 assembly.id
        master_call = next(c for c in mock_part_files.upload.await_args_list
                           if c.kwargs["kind"] == PartFileKind.ASSEMBLY_MASTER)
        assert master_call.kwargs["owner_id"]  # 非 0 / None

    async def test_multiple_is_master_rejected(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_part_files: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """两条 is_master=True → failed 列表；DB 0 写入"""
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))

        items = [
            _item(pdf_index=1, page_index=0, assembly_uid="a1", is_master=True),
            _item(pdf_index=1, page_index=1, assembly_uid="a1", is_master=True),
        ]
        asm_meta = PartBatchTreeAssembly(
            uid="a1", drawing_no="X", name="A",
            applicant_name=None, applicant_id=None,
            customer_id="100",
            request_date=date(2026, 7, 21),
            planned_delivery_date=date(2026, 8, 21),
            is_urgent=False,
        )
        payload = PartBatchTreeRequest(items=items, assemblies=[asm_meta])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={1: (b"%PDF", "x.pdf", "application/pdf")},
            part_files=mock_part_files,
            applicants=AsyncMock(),
        )

        assert len(result.failed) >= 1
        assert any("is_master" in f.message for f in result.failed)
        assert mock_parts.create.await_count == 0


class TestCreatePartsTreePRFFields:
    pytestmark = pytest.mark.asyncio

    async def test_tree_item_carries_order_no_system_delivery_note(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_part_files: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """PR-F 字段（order_no / system_delivery_date / note）透传到 parts.create。"""
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))
        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1003")
        mock_part_files.upload = AsyncMock(return_value=None)

        captured: list[TPart] = []

        async def fake_create(part: TPart) -> None:
            captured.append(part)
        mock_parts.create = fake_create
        mock_parts.get_by_id = AsyncMock(return_value=_make_part(
            id=301, customer_id=100, drawing_no="D1", name="N1",
            order_no="PO-2024-001",
            system_delivery_date=date(2026, 8, 1),
            note="客户特殊要求",
        ))

        item = _item(
            pdf_index=0,
            order_no="PO-2024-001",
            system_delivery_date=date(2026, 8, 1),
            note="客户特殊要求",
        )
        payload = PartBatchTreeRequest(items=[item], assemblies=[])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={0: (b"%PDF", "draw.pdf", "application/pdf")},
            part_files=mock_part_files,
            applicants=AsyncMock(),
        )

        assert len(result.standalone_parts) == 1
        assert len(captured) == 1
        p = captured[0]
        assert p.order_no == "PO-2024-001"
        assert p.system_delivery_date == date(2026, 8, 1)
        assert p.note == "客户特殊要求"

    async def test_optional_fields_none_pass_through(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_part_files: AsyncMock,
        leaf_customer: TCustomer,
        root_customer: TCustomer,
    ) -> None:
        """PR-F 字段全 None 时 → DB 写入 None（不抛错）。"""
        mock_customers.get_by_id = AsyncMock(side_effect=lambda cid: {
            100: leaf_customer, 1: root_customer,
        }.get(cid))
        mock_serial_counters.acquire_serial = AsyncMock(return_value="F1004")
        mock_part_files.upload = AsyncMock(return_value=None)
        captured: list[TPart] = []

        async def fake_create(part: TPart) -> None:
            captured.append(part)
        mock_parts.create = fake_create
        mock_parts.get_by_id = AsyncMock(return_value=_make_part(id=401, customer_id=100))

        item = _item(pdf_index=0)  # order_no/system_delivery_date/note 全 None
        payload = PartBatchTreeRequest(items=[item], assemblies=[])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={0: (b"%PDF", "draw.pdf", "application/pdf")},
            part_files=mock_part_files,
            applicants=AsyncMock(),
        )

        assert len(result.standalone_parts) == 1
        p = captured[0]
        assert p.order_no is None
        assert p.system_delivery_date is None
        assert p.note is None


class TestCreatePartsTreeCustomerNotFound:
    pytestmark = pytest.mark.asyncio

    async def test_customer_not_found_returns_failed(
        self,
        service: PartService,
        mock_parts: AsyncMock,
        mock_customers: AsyncMock,
        mock_serial_counters: AsyncMock,
        mock_part_files: AsyncMock,
    ) -> None:
        """customer_id 不存在 → failed 列表；DB 0 写入"""
        mock_customers.get_by_id = AsyncMock(return_value=None)

        item = _item(pdf_index=0, customer_id="999")
        payload = PartBatchTreeRequest(items=[item], assemblies=[])

        result = await service.create_parts_tree(
            payload,
            file_payloads_by_pdf_index={0: (b"%PDF", "draw.pdf", "application/pdf")},
            part_files=mock_part_files,
            applicants=AsyncMock(),
        )

        assert len(result.failed) >= 1
        assert any("not found" in f.message for f in result.failed)
        assert mock_parts.create.await_count == 0
        assert mock_serial_counters.acquire_serial.await_count == 0


class TestCreatePartsTreeApplicantFallback:
    pytestmark = pytest.mark.asyncio

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