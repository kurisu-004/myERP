"""CNC 编程环节状态机回归测试。

覆盖：
- PENDING → PROGRAMMING：send_to_programming 写 SENT_TO_PROGRAMMING 事件，
  状态字段正确（status=PROGRAMMING, location=OFFICE, current_holder_id=NULL）。
- PROGRAMMING → IN_PROCESS：release_from_programming 复用 ON_SHELF 副作用，
  写 CNC_RELEASED 事件，next_process_id 正确。
- PROGRAMMING 可被 cancel。
- 状态机按 status="PROGRAMMING" 启动时能恢复到 PROGRAMMING 状态。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from model import TCustomer, TPart, TPartEvent, TProcess, TShelf, TShelfProcess
from model.enums import PartEventType, PartStatus, ShelfZone
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository

pytestmark = pytest.mark.asyncio


async def _make_customer(session, name: str) -> TCustomer:
    c = TCustomer(name=name)
    session.add(c)
    await session.flush()
    return c


async def _make_process(session, code: str, name: str) -> TProcess:
    from model.enums import ProcessCategory
    p = TProcess(code=code, name=name, category=ProcessCategory.INHOUSE.value)
    session.add(p)
    await session.flush()
    return p


async def test_send_to_programming_writes_event(clean_db):
    from sqlalchemy import text as _sql_text

    session = clean_db
    await session.execute(_sql_text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_shelf RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_process RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_customer"))
    await session.commit()

    customer = await _make_customer(session, "测试客户")

    part_repo = PartRepository(session)
    event_repo = PartEventRepository(session)
    part = await part_repo.create(
        TPart(
            name="cnc-test",
            drawing_no="D-CNC-1",
            applicant_name="tester",
            quantity=1,
            request_date=date(2026, 7, 6),
            planned_delivery_date=date(2026, 7, 20),
            customer_id=customer.id,
            status=PartStatus.PENDING.value,
        )
    )

    # PENDING → PROGRAMMING
    part.sm.send_to_programming(event_repo=event_repo)
    await part_repo.update(part)

    assert part.status == PartStatus.PROGRAMMING.value
    assert part.location == "OFFICE"
    assert part.current_holder_id is None

    # 断言事件落库
    rows = list(
        (
            await session.execute(
                select(TPartEvent)
                .where(TPartEvent.part_id == part.id)
                .order_by(TPartEvent.id.asc())
            )
        ).scalars().all()
    )
    types = [r.event_type for r in rows]
    assert PartEventType.SENT_TO_PROGRAMMING.value in types, types
    sent = next(r for r in rows if r.event_type == PartEventType.SENT_TO_PROGRAMMING.value)
    assert sent.from_status == PartStatus.PENDING.value
    assert sent.to_status == PartStatus.PROGRAMMING.value


async def test_release_from_programming_writes_cnc_released(clean_db):
    from sqlalchemy import text as _sql_text

    session = clean_db
    await session.execute(_sql_text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_shelf RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_process RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_customer"))
    await session.commit()

    customer = await _make_customer(session, "测试客户")
    shelf_repo = ShelfRepository(session)
    shelf = await shelf_repo.create(
        TShelf(code="PROD-CNC-1", name="CNC 货架", zone=ShelfZone.PRODUCTION.value)
    )
    process_repo = ProcessRepository(session)
    process = await process_repo.create(
        await _make_process(session, "CNC-OP", "CNC 操机")
    )

    part_repo = PartRepository(session)
    event_repo = PartEventRepository(session)
    part = await part_repo.create(
        TPart(
            name="cnc-test-2",
            drawing_no="D-CNC-2",
            applicant_name="tester",
            quantity=1,
            request_date=date(2026, 7, 6),
            planned_delivery_date=date(2026, 7, 20),
            customer_id=customer.id,
            status=PartStatus.PROGRAMMING.value,  # 直接从 PROGRAMMING 起步
            location="OFFICE",
        )
    )

    # 状态机 init 时按 status="PROGRAMMING" 恢复到 PROGRAMMING
    # （不直接断言 sm.current_state，避免依赖 python-statemachine 内部 API）

    # PROGRAMMING → IN_PROCESS
    part.sm.release_from_programming(
        shelf=shelf, process=process, event_repo=event_repo,
    )
    await part_repo.update(part)

    assert part.status == PartStatus.IN_PROCESS.value
    assert part.location == "PRODUCTION_SHELF"
    assert part.current_holder_id == shelf.id
    assert part.next_process_id == process.id

    rows = list(
        (
            await session.execute(
                select(TPartEvent)
                .where(TPartEvent.part_id == part.id)
                .order_by(TPartEvent.id.asc())
            )
        ).scalars().all()
    )
    types = [r.event_type for r in rows]
    assert PartEventType.CNC_RELEASED.value in types, types
    cnc = next(r for r in rows if r.event_type == PartEventType.CNC_RELEASED.value)
    assert cnc.from_status == PartStatus.PROGRAMMING.value
    assert cnc.to_status == PartStatus.IN_PROCESS.value
    # note 包含 shelf code 和 process code
    assert shelf.code in (cnc.note or ""), cnc.note
    assert process.code in (cnc.note or ""), cnc.note


async def test_programming_can_be_cancelled(clean_db):
    from sqlalchemy import text as _sql_text

    session = clean_db
    await session.execute(_sql_text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_customer"))
    await session.commit()

    customer = await _make_customer(session, "测试客户")
    part_repo = PartRepository(session)
    event_repo = PartEventRepository(session)
    part = await part_repo.create(
        TPart(
            name="cnc-cancel",
            drawing_no="D-CNC-CX",
            applicant_name="tester",
            quantity=1,
            request_date=date(2026, 7, 6),
            planned_delivery_date=date(2026, 7, 20),
            customer_id=customer.id,
            status=PartStatus.PROGRAMMING.value,
            location="OFFICE",
        )
    )

    part.sm.cancel(event_repo=event_repo)
    await part_repo.update(part)

    assert part.status == PartStatus.CANCELLED.value
    assert part.location is None
    assert part.serial_no is None  # 释放流水号


async def _build_release_env(session, *, name: str = "测试客户", code: str = "X"):
    """构造 release_from_programming 集成测试所需的环境：客户/货架/工序/映射。

    返回 (customer, shelf, process, part_svc, files_svc)；tests 自行创建 part。
    """
    from sqlalchemy import text as _sql_text

    from repository.customer import CustomerRepository
    from repository.part_file import PartFileRepository
    from service.part import PartService
    from service.part_file import PartFileService

    await session.execute(_sql_text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_part_file RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_shelf RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_process RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_customer"))
    await session.execute(_sql_text("TRUNCATE TABLE t_serial_counter"))
    await session.commit()

    customer = await _make_customer(session, name)
    shelf_repo = ShelfRepository(session)
    shelf = await shelf_repo.create(
        TShelf(
            code=f"PROD-CNC-{code}", name=f"CNC 货架 {code}",
            zone=ShelfZone.PRODUCTION.value,
        )
    )
    process_repo = ProcessRepository(session)
    process = await process_repo.create(
        await _make_process(session, f"CNC-OP-{code}", f"CNC 操机 {code}")
    )
    # 货架↔工序映射：release_from_programming 落货架时的 _assert_shelf_maps_process 需要
    session.add(TShelfProcess(shelf_id=shelf.id, process_id=process.id, sort_order=0))
    await session.flush()

    files_repo = PartFileRepository(session)
    files_svc = PartFileService(files=files_repo)
    # 屏蔽真实 COS 调用
    from unittest.mock import AsyncMock as _AM, patch as _P
    _P("service.part_file.cos_mod.upload_object", new=_AM()).start()
    part_repo = PartRepository(session)
    part_svc = PartService(
        parts=part_repo,
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=None,
        events=PartEventRepository(session),
        serial_counters=None,
        shelves=shelf_repo,
        processes=process_repo,
        work_types=None,
        work_type_process=None,
        files=files_repo,
        shelf_process_repo=ShelfProcessRepository(session),
        broadcaster=None,
        event_broadcaster=None,
    )

    return customer, shelf, process, part_svc, files_svc


async def _make_programming_part(session, customer, drawing_no: str, serial_no: str = "X0001"):
    """创建一个 PROGRAMMING 状态的零件 + 根批次，用于 release_from_programming 测试。"""
    part = await PartRepository(session).create(
        TPart(
            name=f"cnc-{drawing_no.lower()}",
            drawing_no=drawing_no,
            applicant_name="tester",
            quantity=1,
            request_date=date(2026, 7, 10),
            planned_delivery_date=date(2026, 7, 20),
            customer_id=customer.id,
            serial_no=serial_no,
            status=PartStatus.PROGRAMMING.value,
            location="OFFICE",
        )
    )
    from tests.conftest import seed_root_batch
    await seed_root_batch(session, part)
    return part


async def test_release_from_programming_succeeds_with_no_files_uploaded(clean_db):
    """2026-08-02 起 release_from_programming 不再做文件前置校验。

    集成测试：完全没上传任何 CNC 文件，也能成功下发（PROGRAMMING → IN_PROCESS）。
    """
    from schema.part import PlaceOnShelfRequest

    session = clean_db
    customer, shelf, process, part_svc, _files_svc = await _build_release_env(
        session, code="A",
    )
    part = await _make_programming_part(session, customer, "D-NOFILE", serial_no="X0001")

    out = await part_svc.release_from_programming(
        part_id=part.id,
        data=PlaceOnShelfRequest(shelf_id=shelf.id, next_process_id=process.id),
    )
    assert out.status == PartStatus.IN_PROCESS.value


async def test_release_from_programming_succeeds_with_only_g_code_uploaded(clean_db):
    """集成测试：只上传 G 代码（无设定单）也能成功 release。"""
    from model.enums import PartFileKind
    from schema.part import PlaceOnShelfRequest

    session = clean_db
    customer, shelf, process, part_svc, files_svc = await _build_release_env(
        session, code="B",
    )
    part = await _make_programming_part(session, customer, "D-ONLY-NC", serial_no="X0001")

    await files_svc.upload(
        owner_id=part.id, kind=PartFileKind.G_CODE,
        data=b"%NC", original_filename="p.nc",
        content_type=None,
    )

    out = await part_svc.release_from_programming(
        part_id=part.id,
        data=PlaceOnShelfRequest(shelf_id=shelf.id, next_process_id=process.id),
    )
    assert out.status == PartStatus.IN_PROCESS.value


async def test_release_from_programming_succeeds_with_both_files_uploaded(clean_db):
    """集成测试：G 代码 + 设定单都上传也能成功 release（既有 happy path）。"""
    from model.enums import PartFileKind
    from schema.part import PlaceOnShelfRequest

    session = clean_db
    customer, shelf, process, part_svc, files_svc = await _build_release_env(
        session, code="C",
    )
    part = await _make_programming_part(session, customer, "D-BOTH", serial_no="X0001")

    await files_svc.upload(
        owner_id=part.id, kind=PartFileKind.G_CODE,
        data=b"%NC", original_filename="p.nc",
        content_type=None,
    )
    await files_svc.upload(
        owner_id=part.id, kind=PartFileKind.SETUP_SHEET,
        data=b"%PDF", original_filename="setup.pdf",
        content_type=None,
    )

    out = await part_svc.release_from_programming(
        part_id=part.id,
        data=PlaceOnShelfRequest(shelf_id=shelf.id, next_process_id=process.id),
    )
    assert out.status == PartStatus.IN_PROCESS.value
