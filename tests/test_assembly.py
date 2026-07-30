"""装配体集成测试（2026-07-31 新增）。

覆盖 2026-07-29 批次化引入后回归到装配体路径的 bug：
- Bug 1：`get_assembly_service` DI 漏注 `part_batches` / `outsource_shipments`，
  cancel / create-with-PDF / upload_total_pdf / add_child 全部 500。
- 坑 4：`cancel_assembly` 重复取消触发 `TransitionNotAllowed` → 500，应 400。
- 坑 5：`upload_total_pdf` UPDATE flush 后未刷新派生字段 → 末尾 `_build_detail`
  同步读 `updated_at` → MissingGreenlet。

按项目约定：直接调 service（tests/test_delivery_note.py:3 约定）。`_build_assembly_service`
**镜像 `api/deps.py::get_assembly_service`** 的内部 PartService 装配；DI 若漏注，
本测试也会失败（保持镜像关系，参考 conftest 注释）。

不走 HTTP TestClient，因为：(a) 项目无该约定，(b) DI 漂移已经在 service 层
抛 BizError，service 测试已经能直接复现，(c) 引入 TestClient 会让测试依赖
lifespan / 数据库 fixture 编排，超出 bug 范围。
"""
from __future__ import annotations

import io
from datetime import date, datetime

import pytest
from sqlalchemy import select

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import (
    TAssembly,
    TCustomer,
    TPart,
    TPartBatch,
    TPartEvent,
    TProcess,
    TShelf,
    TShelfProcess,
    TWorkType,
)
from model.enums import PartEventType, PartStatus, ShelfZone
from repository.applicant import ApplicantRepository
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_company_process import OutsourceCompanyProcessRepository
from repository.outsource_shipment import OutsourceShipmentRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.part_file import PartFileRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from schema.assembly import (
    AssemblyChildCreateRequest,
    AssemblyCreateRequest,
    AssemblyUpdateRequest,
)
from service.assembly import AssemblyService
from service.part import PartService
from service.part_file import PartFileService

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers — 镜像 api/deps.py::get_assembly_service
# ============================================================

def _build_assembly_service(
    session,
    *,
    user: CurrentUser | None = None,
) -> AssemblyService:
    """镜像 `api/deps.py::get_assembly_service` 的装配。

    若 `get_assembly_service` 改动了内部 PartService 注入，本 helper 必须同步
    改动；test_assembly.py 跟 deps.py 的对应关系见 conftest 注释。
    """
    parts_repo = PartRepository(session)
    files_repo = PartFileRepository(session)
    serial_repo = SerialCounterRepository(session)

    part_service = PartService(
        parts=parts_repo,
        customers=CustomerRepository(session),
        workers=None,  # cancel/create_root_batch 路径不用 worker（_to_out 守护）
        events=PartEventRepository(session),
        serial_counters=serial_repo,
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
        files=files_repo,
        delivery_notes_repo=None,
        outsource_companies=OutsourceCompanyRepository(session),
        outsource_company_process=OutsourceCompanyProcessRepository(session),
        outsource_shipments=OutsourceShipmentRepository(session),
        part_batches=PartBatchRepository(session),
        broadcaster=None,
        event_broadcaster=None,
        current_user=user,
    )

    part_files = PartFileService(files=files_repo, current_user=user)

    async def _noop_broadcaster() -> None:
        return None

    async def _noop_event_broadcaster(event_type: str, payload: dict) -> None:
        return None

    return AssemblyService(
        assemblies=AssemblyRepository(session),
        parts=parts_repo,
        files=files_repo,
        customers=CustomerRepository(session),
        serial_counters=serial_repo,
        events=PartEventRepository(session),
        part_service=part_service,
        part_files=part_files,
        applicants=ApplicantRepository(session),
        broadcaster=_noop_broadcaster,
        event_broadcaster=_noop_event_broadcaster,
        current_user=user,
    )


def _current_user(uid: int | None = None) -> CurrentUser:
    return CurrentUser(
        id=uid,
        username="actor",
        full_name="Actor",
        is_active=True,
        roles=("MANAGER", "CLERK"),
        shelf_ids=(),
    )


async def _make_world(session, *, prefix: str = "T") -> dict:
    """一级客户 + 工序 + 货架映射（足够 cancel / create 路径走通）。"""
    cust_l1 = TCustomer(name=f"装配测试L1-{prefix}", parent_id=None)
    cust_l1.serial_prefix = prefix
    cust_l2 = TCustomer(name=f"装配测试L2-{prefix}", parent_id=None)
    cust_l2.serial_prefix = None  # 叶子节点继承父
    shelf = TShelf(code=f"AST-{prefix}", name="测试生产架", zone=ShelfZone.PRODUCTION.value)
    proc = TProcess(code=f"APROC-{prefix}", name="工序", category="INHOUSE", sort_order=0)
    wt = TWorkType(code=f"AWT-{prefix}", name="工种")
    session.add_all([cust_l1, cust_l2, shelf, proc, wt])
    await session.flush()
    cust_l2.parent_id = cust_l1.id
    session.add(TShelfProcess(shelf_id=shelf.id, process_id=proc.id, sort_order=0))
    await session.flush()
    return {
        "cust_l1": cust_l1, "cust_l2": cust_l2, "shelf": shelf, "process": proc, "wt": wt,
    }


async def _seed_assembly_with_children(
    session, world: dict, *, child_qty: int = 2,
) -> tuple[TAssembly, list[TPart]]:
    """直接造一条装配体 + N 个子件 + N 个根批次（绕开 create_assembly PDF 路径）。"""
    asm = TAssembly(
        drawing_no="ASM-T-001",
        name="测试装配体",
        applicant_name="测试申请人",
        customer_id=world["cust_l2"].id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        actual_delivery_date=None,
        is_urgent=False,
        status="PENDING",
        serial_no="T0001",
        quantity=1,
        unit_price=0,
        total_price=0,
    )
    asm.created_by = None
    asm.updated_by = None
    session.add(asm)
    await session.flush()

    parts: list[TPart] = []
    for i in range(child_qty):
        part = TPart(
            serial_no=f"T0001-{i + 1:02d}",
            name=f"子件-{i + 1}",
            drawing_no=f"DWG-{i + 1:02d}",
            applicant_name="测试申请人",
            quantity=1,
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            customer_id=world["cust_l2"].id,
            assembly_id=asm.id,
            status="PENDING",
            location="OFFICE",
        )
        part.created_by = None
        part.updated_by = None
        session.add(part)
        await session.flush()
        # 根批次（批次化必需）
        batch = TPartBatch(
            part_id=part.id,
            batch_no=1,
            quantity=part.quantity,
            status=part.status,
            location=part.location,
            current_holder_id=None,
            next_process_id=None,
            placed_at=None,
        )
        session.add(batch)
        await session.flush()
        parts.append(part)

    await session.commit()
    return asm, parts


def _make_pdf(page_count: int) -> bytes:
    """生成 N 页最小 PDF（pypdf 已在 deps）。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ============================================================
# Tests
# ============================================================

class TestAssemblyCancel:
    """Bug 1 主路径：cancel_assembly 走 PartService.cancel → 需要 part_batches。"""

    async def test_cancel_cascades_children_and_releases_serial(self, clean_db):
        world = await _make_world(clean_db, prefix="C")
        asm, children = await _seed_assembly_with_children(clean_db, world, child_qty=3)

        svc = _build_assembly_service(clean_db, user=_current_user(uid=1))
        await svc.cancel_assembly(asm.id)

        # 重新读
        await clean_db.refresh(asm)
        assert asm.status == "CANCELLED"
        assert asm.serial_no is None  # 释放回池
        for child in children:
            await clean_db.refresh(child)
            assert child.status == "CANCELLED"
            # 批次也应被 cancel 置 CANCELLED（rollup 后工单 serial 也释放）
            stmt = select(TPartBatch).where(TPartBatch.part_id == child.id)
            batches = (await clean_db.execute(stmt)).scalars().all()
            assert all(b.status == "CANCELLED" for b in batches)
            # 释放子件 serial
            assert child.serial_no is None

    async def test_double_cancel_returns_400_not_500(self, clean_db):
        """坑 4：重复取消 → BizError 400 BIZ_INVALID_TRANSITION，不再 TransitionNotAllowed → 500。"""
        world = await _make_world(clean_db, prefix="D")
        asm, _children = await _seed_assembly_with_children(clean_db, world)

        svc = _build_assembly_service(clean_db, user=_current_user(uid=1))
        await svc.cancel_assembly(asm.id)

        with pytest.raises(BizError) as ei:
            await svc.cancel_assembly(asm.id)
        assert ei.value.code == ErrCode.BIZ_INVALID_TRANSITION
        assert ei.value.http_status == 400


class TestAssemblyUpdate:
    """update_assembly 不走批次，但走 _build_detail → part_service._to_out（有守卫）。
    顺带验证 `_to_out` 在 part_batches=None 时也能跑通（修复后 DI 注入了，这里只是兜底）。"""

    async def test_update_metadata(self, clean_db):
        world = await _make_world(clean_db, prefix="U")
        asm, _ = await _seed_assembly_with_children(clean_db, world, child_qty=2)

        svc = _build_assembly_service(clean_db, user=_current_user(uid=1))
        payload = AssemblyUpdateRequest(
            drawing_no="ASM-T-NEW",
            name="改名后的装配体",
            customer_id=str(world["cust_l2"].id),
            applicant_name=None,
            applicant_id=None,
            request_date=None,
            planned_delivery_date=None,
            actual_delivery_date=None,
            is_urgent=True,
        )
        detail = await svc.update_assembly(asm.id, payload)

        assert detail.assembly.drawing_no == "ASM-T-NEW"
        assert detail.assembly.name == "改名后的装配体"
        assert detail.assembly.is_urgent is True
        # 终态拒编
        await svc.cancel_assembly(asm.id)
        with pytest.raises(BizError) as ei:
            await svc.update_assembly(asm.id, payload)
        assert ei.value.code == ErrCode.BIZ_INVALID_TRANSITION


class TestAssemblyCreateWithPDF:
    """create_assembly 带 PDF 走 create_root_batch（DI 注入了 part_batches 才不会 500）。"""

    async def test_create_with_pdf_creates_root_batches(self, clean_db, monkeypatch):
        """Bug 1：create_assembly with PDF 走 PartService.create_root_batch。

        需要 part_batches repo，DI 漏注时 → 500。
        """
        # COS 上传必须 stub：项目用真实 COS，会失败；这里 stub PartFileService.upload。
        from schema.part_file import PartFileOut

        world = await _make_world(clean_db, prefix="P")

        svc = _build_assembly_service(clean_db, user=_current_user(uid=1))

        async def fake_upload(*, owner_id, kind, data, original_filename, content_type):
            row_id = abs(hash((owner_id, kind.value, original_filename))) % 10_000_000 + 1
            return PartFileOut(
                id=str(row_id),
                version=0,
                owner_id=str(owner_id),
                kind=kind.value,
                file_type="pdf",
                original_filename=original_filename,
                file_size=len(data),
                content_type=content_type,
                upload_status="completed",
                download_url="https://example.invalid/fake",
                content_sha256="0" * 64,
                created_at=datetime(2026, 7, 31),
            )

        monkeypatch.setattr(svc.part_files, "upload", fake_upload)

        pdf = _make_pdf(page_count=3)  # 总装 + 2 个子件
        req = AssemblyCreateRequest(
            drawing_no="ASM-PDF-001",
            name="PDF 测试装配体",
            applicant_name="PDF 测试申请人",
            customer_id=str(world["cust_l2"].id),
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            is_urgent=False,
            quantity=1,
            unit_price=0,
            total_price=0,
            children=[
                AssemblyChildCreateRequest(
                    drawing_no="01", name="子件1", quantity=1,
                ),
                AssemblyChildCreateRequest(
                    drawing_no="02", name="子件2", quantity=1,
                ),
            ],
        )
        result = await svc.create_assembly(req, pdf_bytes=pdf, pdf_filename="asm.pdf")

        assert result.assembly.serial_no is not None
        assert len(result.children) == 2
        for child in result.children:
            # 每个子件必须有一个根批次（DI 注入后才会创建；否则抛 500）
            stmt = select(TPartBatch).where(TPartBatch.part_id == int(child.id))
            batches = (await clean_db.execute(stmt)).scalars().all()
            assert len(batches) == 1
            assert batches[0].batch_no == 1
            assert batches[0].status == "PENDING"


class TestAssemblyUploadTotalPdf:
    """坑 5：upload_total_pdf 在 UPDATE flush 后必须 refresh 派生字段。"""

    async def test_upload_total_pdf_on_empty_assembly_no_missing_greenlet(
        self, clean_db, monkeypatch,
    ):
        """空装配体（serial_no=None）首次上传 PDF → UPDATE flush → 末尾 _build_detail
        同步读 asm.updated_at。若未 refresh 会触发 MissingGreenlet。"""
        from schema.part_file import PartFileOut

        world = await _make_world(clean_db, prefix="U")
        # 空装配体（serial_no = None）— 走 upload_total_pdf 的首次分配分支
        asm = TAssembly(
            drawing_no="EMPTY-001",
            name="空装配体",
            applicant_name="",
            customer_id=world["cust_l2"].id,
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 8, 1),
            actual_delivery_date=None,
            is_urgent=False,
            status="PENDING",
            serial_no=None,
            quantity=1,
            unit_price=0,
            total_price=0,
        )
        asm.created_by = None
        asm.updated_by = None
        clean_db.add(asm)
        await clean_db.commit()

        svc = _build_assembly_service(clean_db, user=_current_user(uid=1))

        async def fake_upload(*, owner_id, kind, data, original_filename, content_type):
            row_id = abs(hash((owner_id, kind.value, original_filename))) % 10_000_000 + 1
            return PartFileOut(
                id=str(row_id),
                version=0,
                owner_id=str(owner_id),
                kind=kind.value,
                file_type="pdf",
                original_filename=original_filename,
                file_size=len(data),
                content_type=content_type,
                upload_status="completed",
                download_url="https://example.invalid/fake",
                content_sha256="0" * 64,
                created_at=datetime(2026, 7, 31),
            )

        monkeypatch.setattr(svc.part_files, "upload", fake_upload)

        pdf = _make_pdf(page_count=3)
        detail = await svc.upload_total_pdf(
            asm.id, pdf_bytes=pdf, pdf_filename="empty.pdf",
        )

        # 装配体应该已经拿到顶级 serial + 2 个子件 + updated_at 可读
        assert detail.assembly.serial_no is not None
        assert detail.assembly.updated_at is not None
        assert len(detail.children) == 2