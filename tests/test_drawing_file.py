"""DrawingService 的集成测试（multipart + 后端直传 COS 模式）。"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from model import TCustomer, TDrawingFile, TPart
from repository import (
    AssemblyRepository,
    CustomerRepository,
    DrawingFileRepository,
    PartRepository,
)
from service import DrawingService
from tests.conftest import FakeCosClient
from utils.id_gen import new_id


pytestmark = pytest.mark.integration


async def _seed_customer_and_part(session: AsyncSession) -> tuple[int, int]:
    parent = TCustomer(id=new_id(), name="法拉电子", parent_id=None)
    leaf = TCustomer(id=new_id(), name="母排厂", parent_id=parent.id)
    session.add_all([parent, leaf])
    await session.flush()
    part = TPart(
        id=new_id(),
        serial_no="F1000",
        name="基础板",
        drawing_no="TEST-1",
        applicant_name="x",
        quantity=1,
        unit_price=100,
        total_price=100,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
        status="PENDING",
        is_urgent=False,
        customer_id=leaf.id,
    )
    session.add(part)
    await session.commit()
    return leaf.id, part.id


def _drawings_service(session: AsyncSession) -> DrawingService:
    return DrawingService(
        files=DrawingFileRepository(session),
        parts=PartRepository(session),
        assemblies=AssemblyRepository(session),
    )


# ============================================================
# 1. 上传 .step 到 part → part_id 非空、COS put 1 次
# ============================================================
async def test_upload_step_to_part(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    _, part_id = await _seed_customer_and_part(clean_db)
    svc = _drawings_service(clean_db)
    out = await svc.upload_to_part(
        part_id,
        data=b"ISO-10303-21 fake step content",
        original_filename="part.step",
        content_type=None,
    )
    assert out.file_type == "STEP"
    assert out.owner_type == "part"
    assert out.owner_id == part_id
    assert len(fake_cos.put_calls) == 1
    _, key, size, ct = fake_cos.put_calls[0]
    assert key.startswith(f"drawings/part/{part_id}/")
    assert size > 0
    # 即时签的 download_url 形如 fake.cos.example
    assert out.download_url.startswith("https://fake.cos.example/")

    row = (
        await clean_db.execute(
            select(TDrawingFile).where(TDrawingFile.id == out.id)
        )
    ).scalar_one()
    assert row.part_id == part_id and row.assembly_id is None


# ============================================================
# 2. 上传到装配件 → assembly_id 非空、part_id NULL
# ============================================================
async def test_upload_to_assembly(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    from model import TAssembly
    parent = TCustomer(id=new_id(), name="法拉电子", parent_id=None)
    leaf = TCustomer(id=new_id(), name="母排厂", parent_id=parent.id)
    asm = TAssembly(
        id=new_id(),
        drawing_no="A-1",
        name="A",
        customer_id=leaf.id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 15),
    )
    clean_db.add_all([parent, leaf, asm])
    await clean_db.commit()

    svc = _drawings_service(clean_db)
    out = await svc.upload_to_assembly(
        asm.id,
        data=b"extra file",
        original_filename="extra.dwg",
        content_type=None,
    )
    assert out.owner_type == "assembly"
    assert out.owner_id == asm.id
    assert out.file_type == "DWG"
    row = (
        await clean_db.execute(
            select(TDrawingFile).where(TDrawingFile.id == out.id)
        )
    ).scalar_one()
    assert row.assembly_id == asm.id and row.part_id is None


# ============================================================
# 3. 文件大小超过 COS_MAX_FILE_SIZE → BIZ_DRAWING_FILE_TOO_LARGE
# ============================================================
async def test_upload_file_too_large(
    clean_db: AsyncSession, fake_cos: FakeCosClient, monkeypatch
) -> None:
    _, part_id = await _seed_customer_and_part(clean_db)
    monkeypatch.setattr("core.config.settings.cos_max_file_size_bytes", 10)
    svc = _drawings_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.upload_to_part(
            part_id,
            data=b"x" * 11,
            original_filename="big.step",
            content_type=None,
        )
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_TOO_LARGE


# ============================================================
# 4. 非法扩展名 → BIZ_DRAWING_FILE_BAD_TYPE
# ============================================================
async def test_upload_bad_type(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    _, part_id = await _seed_customer_and_part(clean_db)
    svc = _drawings_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.upload_to_part(
            part_id,
            data=b"exe content",
            original_filename="malware.exe",
            content_type=None,
        )
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_BAD_TYPE


# ============================================================
# 5. list 文件返回带 presigned URL
# ============================================================
async def test_list_files_returns_presigned_urls(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    _, part_id = await _seed_customer_and_part(clean_db)
    svc = _drawings_service(clean_db)
    await svc.upload_to_part(
        part_id, data=b"a", original_filename="a.step", content_type=None
    )
    await svc.upload_to_part(
        part_id, data=b"b", original_filename="b.step", content_type=None
    )
    items = await svc.list_for_part(part_id)
    assert len(items) == 2
    for it in items:
        assert it.download_url.startswith("https://fake.cos.example/")
        assert it.owner_type == "part"
        assert it.owner_id == part_id


# ============================================================
# 6. 软删文件后再 list 不返回
# ============================================================
async def test_soft_delete_file(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    _, part_id = await _seed_customer_and_part(clean_db)
    svc = _drawings_service(clean_db)
    out = await svc.upload_to_part(
        part_id, data=b"x", original_filename="x.step", content_type=None
    )
    await svc.delete_file(out.id)
    items = await svc.list_for_part(part_id)
    assert items == []
    # COS key 被尝试删除（fire-and-forget → 这里同步立即执行，但 create_task 调度是异步的）
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fake_cos.delete_calls  # 至少被调过一次


# ============================================================
# 7. get_download_url 对不存在的 file → BIZ_DRAWING_FILE_NOT_FOUND
# ============================================================
async def test_get_download_url_not_found(
    clean_db: AsyncSession, fake_cos: FakeCosClient
) -> None:
    svc = _drawings_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.get_download_url(99999999)
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_NOT_FOUND
