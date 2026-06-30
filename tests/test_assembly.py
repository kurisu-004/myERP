"""AssemblyService 的集成测试（multipart + 后端直传 COS 模式）。

依赖：
- 本地 PostgreSQL 已起（`docker compose up -d`），迁移已 `uv run alembic upgrade head`。
- 走 `clean_db` fixture 会清空装配相关表；**会破坏本地数据**。
- `fake_cos` 把 core.cos 替换成内存 FakeCosClient。

每个测试独立 DB session；用完自动 commit / rollback。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from model import TAssembly, TDrawingFile, TPart, TPartEvent, TCustomer
from repository import (
    AssemblyRepository,
    CustomerRepository,
    DrawingFileRepository,
    PartEventRepository,
    PartRepository,
    SerialCounterRepository,
    WorkerRepository,
)
from schema.assembly import AssemblyCreateRequest, AssemblyChildCreateRequest
from service import AssemblyService, DrawingService, PartService
from tests.conftest import FakeCosClient
from utils.id_gen import new_id


pytestmark = pytest.mark.integration


# ============================================================
# helpers
# ============================================================
async def _seed_leaf_customer(session: AsyncSession) -> tuple[int, int]:
    """建一个一级集团 + 一个二级叶子节点；返回 (parent_id, child_id)。"""
    parent = TCustomer(id=new_id(), name="法拉电子", parent_id=None)
    child = TCustomer(id=new_id(), name="母排厂", parent_id=parent.id)
    session.add_all([parent, child])
    await session.flush()
    return parent.id, child.id


def _build_service(session: AsyncSession, fake: FakeCosClient) -> AssemblyService:
    from api.v1.ws import broadcast_dashboard_event

    async def _broadcaster(et, payload):
        await broadcast_dashboard_event(et, payload)

    parts = PartRepository(session)
    files = DrawingFileRepository(session)
    asm_repo = AssemblyRepository(session)
    cust = CustomerRepository(session)
    sc = SerialCounterRepository(session)
    ev = PartEventRepository(session)
    wk = WorkerRepository(session)
    ps = PartService(parts=parts, customers=cust, workers=wk, events=ev, serial_counters=sc)
    ds = DrawingService(files=files, parts=parts, assemblies=asm_repo)
    return AssemblyService(
        assemblies=asm_repo,
        parts=parts,
        files=files,
        customers=cust,
        serial_counters=sc,
        events=ev,
        part_service=ps,
        drawings=ds,
        event_broadcaster=_broadcaster,
    )


# ============================================================
# 1. happy path
# ============================================================
async def test_create_assembly_happy_path(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()

    svc = _build_service(clean_db, fake_cos)
    pdf_bytes = b"%PDF-1.4\nfake-pdf-bytes\n"
    req = AssemblyCreateRequest(
        name="精研挡料座",
        drawing_no="E42FX1020107101",
        customer_id=leaf_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        is_urgent=False,
        children=[
            AssemblyChildCreateRequest(
                drawing_no="E42FX1020107101-1",
                name="基础板",
                quantity=1,
                unit_price=100,
                page_index=2,
            ),
            AssemblyChildCreateRequest(
                drawing_no="E42FX1020107101-2",
                name="挡块",
                quantity=1,
                unit_price=80,
                page_index=3,
            ),
        ],
    )
    result = await svc.create_assembly(
        req, pdf_bytes=pdf_bytes, pdf_filename="E42FX1020107101精研挡料座.pdf"
    )

    # 1 t_assembly + 2 t_part + 3 t_drawing_file (1 装配件 PDF + 2 子件 page_index 引用)
    asm_count = (
        await clean_db.execute(select(TAssembly).where(TAssembly.id == result.assembly.id))
    ).scalar_one()
    assert asm_count is not None
    assert asm_count.drawing_no == "E42FX1020107101"
    assert asm_count.customer_id == leaf_id

    children = (
        (
            await clean_db.execute(
                select(TPart).where(TPart.assembly_id == result.assembly.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(children) == 2
    drawing_nos = sorted(c.drawing_no for c in children)
    assert drawing_nos == ["E42FX1020107101-1", "E42FX1020107101-2"]
    # 每个子件都有序列号（prefix=parent_id 决定）
    assert all(c.serial_no is not None and c.serial_no.startswith("F") for c in children)
    # 每个子件都有 CREATED 事件
    events = (
        (
            await clean_db.execute(
                select(TPartEvent).where(TPartEvent.part_id.in_([c.id for c in children]))
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2
    assert all(e.event_type == "CREATED" for e in events)

    # t_drawing_file：1 装配件 PDF + 2 子件 page_index 引用 = 3 行
    files = (
        (
            await clean_db.execute(
                select(TDrawingFile).where(
                    (TDrawingFile.assembly_id == result.assembly.id)
                    | (TDrawingFile.part_id.in_([c.id for c in children]))
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(files) == 3
    asm_pdf = [f for f in files if f.assembly_id == result.assembly.id]
    assert len(asm_pdf) == 1 and asm_pdf[0].page_index is None
    child_refs = [f for f in files if f.part_id is not None]
    assert {f.page_index for f in child_refs} == {2, 3}
    # 三个文件指向同一个 COS object
    assert len({f.object_key for f in files}) == 1

    # COS 只调一次 put_object（整本 PDF 上传一次）
    assert len(fake_cos.put_calls) == 1
    bucket, key, size, content_type = fake_cos.put_calls[0]
    assert key.startswith(f"drawings/assembly/{result.assembly.id}/")
    assert size == len(pdf_bytes)

    # result 拼装正确
    assert len(result.children) == 2
    assert all(c.assembly_id == result.assembly.id for c in result.children)
    assert len(result.files) == 3
    # 每个 file 都带 download_url
    for f in result.files:
        assert f.download_url.startswith("https://fake.cos.example/")


# ============================================================
# 2. 客户是一级集团 → BIZ_ASSEMBLY_BAD_CUSTOMER
# ============================================================
async def test_create_assembly_parent_customer_rejected(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, _ = await _seed_leaf_customer(clean_db)
    await clean_db.commit()

    svc = _build_service(clean_db, fake_cos)
    req = AssemblyCreateRequest(
        name="X",
        drawing_no="X-1",
        customer_id=parent_id,  # 一级，不是叶子
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        children=[
            AssemblyChildCreateRequest(
                drawing_no="X-1-1", name="p1", page_index=2
            )
        ],
    )
    with pytest.raises(BizError) as exc_info:
        await svc.create_assembly(
            req, pdf_bytes=b"%PDF-1.4\n", pdf_filename="x.pdf"
        )
    assert exc_info.value.code == ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER


# ============================================================
# 3. children=[] → Pydantic min_length 校验（422）；service 层没机会处理
# ============================================================
async def test_create_assembly_empty_children_rejected_at_schema(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AssemblyCreateRequest(
            name="X",
            drawing_no="X-1",
            customer_id=leaf_id,
            request_date=date(2026, 7, 1),
            planned_delivery_date=date(2026, 7, 15),
            children=[],
        )


# ============================================================
# 4. PDF 不是 .pdf 后缀 → BIZ_DRAWING_FILE_BAD_TYPE
# ============================================================
async def test_create_assembly_non_pdf_rejected(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()

    svc = _build_service(clean_db, fake_cos)
    req = AssemblyCreateRequest(
        name="X",
        drawing_no="X-1",
        customer_id=leaf_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        children=[
            AssemblyChildCreateRequest(drawing_no="X-1-1", name="p1", page_index=2)
        ],
    )
    with pytest.raises(BizError) as exc_info:
        await svc.create_assembly(
            req, pdf_bytes=b"fake-step-content", pdf_filename="model.step"
        )
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_BAD_TYPE


# ============================================================
# 5. 详情：装配件自身 / 任一子件反查都拿到同一份 AssemblyDetail
# ============================================================
async def test_get_assembly_for_child_returns_same_detail(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()
    svc = _build_service(clean_db, fake_cos)
    req = AssemblyCreateRequest(
        name="A",
        drawing_no="A-1",
        customer_id=leaf_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        children=[
            AssemblyChildCreateRequest(drawing_no="A-1-1", name="p1", page_index=2),
            AssemblyChildCreateRequest(drawing_no="A-1-2", name="p2", page_index=3),
        ],
    )
    result = await svc.create_assembly(
        req, pdf_bytes=b"%PDF\n", pdf_filename="a.pdf"
    )

    detail_self = await svc.get_assembly_detail(result.assembly.id)
    child = result.children[0]
    detail_child = await svc.get_assembly_for_child(child.id)

    assert detail_self.assembly.id == detail_child.assembly.id
    assert len(detail_self.children) == len(detail_child.children) == 2
    assert len(detail_self.files) == len(detail_child.files) == 3
    for f in detail_self.files:
        assert f.download_url.startswith("https://fake.cos.example/")


# ============================================================
# 6. 普通零件（非子件）反查 → BIZ_ASSEMBLY_NOT_FOUND
# ============================================================
async def test_get_assembly_for_non_child_part(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()
    # 直接往 DB 插一条非子件零件
    part = TPart(
        id=new_id(),
        serial_no="F1000",
        name="lonely",
        drawing_no="LONELY-1",
        applicant_name="x",
        quantity=1,
        unit_price=0,
        total_price=0,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        status="PENDING",
        is_urgent=False,
        customer_id=leaf_id,
        assembly_id=None,
    )
    clean_db.add(part)
    await clean_db.commit()

    svc = _build_service(clean_db, fake_cos)
    with pytest.raises(BizError) as exc_info:
        await svc.get_assembly_for_child(part.id)
    assert exc_info.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND


# ============================================================
# 7. 级联软删：装配件 + 子件 + 文件
# ============================================================
async def test_soft_delete_assembly_cascades(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()
    svc = _build_service(clean_db, fake_cos)
    req = AssemblyCreateRequest(
        name="A",
        drawing_no="A-1",
        customer_id=leaf_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        children=[
            AssemblyChildCreateRequest(drawing_no="A-1-1", name="p1", page_index=2),
        ],
    )
    result = await svc.create_assembly(
        req, pdf_bytes=b"%PDF\n", pdf_filename="a.pdf"
    )

    await svc.soft_delete_assembly(result.assembly.id)

    asm = (
        await clean_db.execute(
            select(TAssembly).where(TAssembly.id == result.assembly.id)
        )
    ).scalar_one()
    assert asm.deleted_at is not None

    children = (
        (
            await clean_db.execute(
                select(TPart).where(TPart.assembly_id == result.assembly.id)
            )
        )
        .scalars()
        .all()
    )
    assert all(c.deleted_at is not None for c in children)

    files = (
        (
            await clean_db.execute(
                select(TDrawingFile).where(
                    (TDrawingFile.assembly_id == result.assembly.id)
                    | (
                        TDrawingFile.part_id.in_([c.id for c in children])
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert all(f.deleted_at is not None for f in files)
    # 软删触发 fire-and-forget delete_object
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fake_cos.delete_calls  # 至少被调过一次


# ============================================================
# 8. COS 上传失败 → 整体事务回滚，无残留
# ============================================================
async def test_pdf_upload_failure_rolls_back(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    parent_id, leaf_id = await _seed_leaf_customer(clean_db)
    await clean_db.commit()

    # 改用 monkeypatch 把 put_object 替换为抛 BizError 的版本。
    original_put = fake_cos.put_object

    def boom_put(Bucket, Key, Body, **kw):  # noqa: N803
        from core.error_code import ErrCode
        from core.exception import BizError

        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"simulated {Key}",
            http_status=502,
        )

    fake_cos.put_object = boom_put  # type: ignore[assignment]

    svc = _build_service(clean_db, fake_cos)
    req = AssemblyCreateRequest(
        name="X",
        drawing_no="X-1",
        customer_id=leaf_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        children=[
            AssemblyChildCreateRequest(drawing_no="X-1-1", name="p1", page_index=2)
        ],
    )
    with pytest.raises(BizError) as exc_info:
        await svc.create_assembly(
            req, pdf_bytes=b"%PDF\n", pdf_filename="x.pdf"
        )
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_UPLOAD_FAILED

    # pytest.raises 把异常吞了，db_session 的 except 不会触发；
    # 显式回滚让测试间干净。
    await clean_db.rollback()

    # 验证 t_assembly / t_part / t_drawing_file 都没新增行
    asm_count = (
        await clean_db.execute(text("SELECT count(*) FROM t_assembly"))
    ).scalar_one()
    part_count = (
        await clean_db.execute(text("SELECT count(*) FROM t_part"))
    ).scalar_one()
    file_count = (
        await clean_db.execute(text("SELECT count(*) FROM t_drawing_file"))
    ).scalar_one()
    assert asm_count == 0
    assert part_count == 0
    assert file_count == 0

    fake_cos.put_object = original_put  # type: ignore[assignment]
