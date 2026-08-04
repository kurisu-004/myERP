"""零件召回（2026-08-05）集成测试。

覆盖：
- ON_SHELF → PENDING（M/C）
- PROGRAMMING → PENDING（M/C）
- ON_SHELF → PROGRAMMING（M/CNC）
- WITH_WORKER 批次召回 → BIZ_INVALID_TRANSITION
- 多在架批次不带 batch_id → BIZ_INVALID_VALUE；带 batch_id → 只召回该批次
- 召回保留 serial_no、不写释放事件
- 召回后 next_process_id / placed_at / holder 清空
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from core.error_code import ErrCode
from core.exception import BizError
from model import (
    TCustomer,
    TPart,
    TPartBatch,
    TPartEvent,
    TProcess,
    TShelf,
    TShelfProcess,
    TWorker,
    TWorkType,
)
from model.enums import PartEventType, ShelfZone
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from schema.part import (
    PartPickUpRequest,
    PlaceOnShelfRequest,
)
from service.part import PartService
from tests.conftest import seed_root_batch

pytestmark = pytest.mark.asyncio


# ============================================================
# 复用 test_part_batch.py 的 helper 风格（独立 fixture 互不影响）
# ============================================================
async def _make_world(session, *, prefix: str = "R"):
    cust = TCustomer(name=f"召回测试客户-{prefix}", parent_id=None)
    cust.serial_prefix = prefix
    shelf = TShelf(code=f"RC-{prefix}1", name="生产架", zone=ShelfZone.PRODUCTION.value)
    insp_shelf = TShelf(code=f"RC-{prefix}2", name="品检架", zone=ShelfZone.INSPECTION.value)
    process = TProcess(code=f"PROC-{prefix}1", name="工序1", category="INHOUSE", sort_order=0)
    process2 = TProcess(code=f"PROC-{prefix}2", name="工序2", category="INHOUSE", sort_order=1)
    wt = TWorkType(code=f"WT-{prefix}", name="工种")
    worker = TWorker(
        badge_code=f"BDG-{prefix}", name="召回工人", is_active=True,
    )
    session.add_all([cust, shelf, insp_shelf, process, process2, wt, worker])
    await session.flush()
    session.add(TShelfProcess(shelf_id=shelf.id, process_id=process.id, sort_order=0))
    session.add(TShelfProcess(shelf_id=shelf.id, process_id=process2.id, sort_order=1))
    worker.work_type_id = wt.id
    await session.flush()
    return {
        "customer": cust, "shelf": shelf, "insp_shelf": insp_shelf,
        "process": process, "process2": process2, "wt": wt, "worker": worker,
    }


def _make_service(session) -> PartService:
    return PartService(
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        events=PartEventRepository(session),
        serial_counters=SerialCounterRepository(session),
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        work_types=WorkTypeRepository(session),
        work_type_process=WorkTypeProcessRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
        broadcaster=None,
        event_broadcaster=None,
    )


async def _make_part(
    session, customer: TCustomer, *, qty: int = 10, serial: str = "R0001",
) -> TPart:
    part = TPart(
        serial_no=serial,
        name=f"召回件-{serial}",
        drawing_no=f"DWG-{serial}",
        applicant_name="召回申请人",
        quantity=qty,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        customer_id=customer.id,
        status="PENDING",
        location="OFFICE",
    )
    session.add(part)
    await session.flush()
    await seed_root_batch(session, part)
    return part


async def _batches(session, part_id: int) -> list[TPartBatch]:
    rows = await session.execute(
        select(TPartBatch)
        .where(TPartBatch.part_id == part_id, TPartBatch.deleted_at.is_(None))
        .order_by(TPartBatch.batch_no.asc())
    )
    return list(rows.scalars().all())


async def _events(session, part_id: int) -> list[TPartEvent]:
    rows = await session.execute(
        select(TPartEvent)
        .where(TPartEvent.part_id == part_id)
        .order_by(TPartEvent.id.asc())
    )
    return list(rows.scalars().all())


async def _place(svc, part, world, *, qty=None):
    return await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world["shelf"].id,
            next_process_id=world["process"].id,
            quantity=qty,
        ),
    )


# ============================================================
# ON_SHELF → PENDING
# ============================================================
async def test_recall_on_shelf_to_pending(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=10)
    svc = _make_service(clean_db)

    await _place(svc, part, world)
    assert part.status == "IN_PROCESS"
    assert part.location == "PRODUCTION_SHELF"
    assert part.current_holder_id == world["shelf"].id
    assert part.next_process_id == world["process"].id
    serial_no_before = part.serial_no

    # 批次上 placed_at 已被 on_enter_ON_SHELF 设置；part.placed_at 始终 None（rollup 不复制）
    on_shelf_batch = (await _batches(clean_db, part.id))[0]
    assert on_shelf_batch.placed_at is not None

    out = await svc.recall_to_pending(part.id)
    assert out.id == part.id
    assert out.status == "PENDING"
    assert out.location == "OFFICE"
    assert out.current_holder_id is None
    assert out.next_process_id is None
    # serial_no 保留（非终态召回不释放）
    assert out.serial_no == serial_no_before

    # 批次同步：唯一 ON_SHELF 批次 → PENDING；placed_at 由 on_enter_PENDING 清空
    batches = await _batches(clean_db, part.id)
    assert len(batches) == 1
    assert batches[0].status == "PENDING"
    assert batches[0].location == "OFFICE"
    assert batches[0].current_holder_id is None
    assert batches[0].next_process_id is None
    assert batches[0].placed_at is None

    # 事件：RECALLED + from_status=IN_PROCESS, to_status=PENDING
    events = await _events(clean_db, part.id)
    recall_events = [e for e in events if e.event_type == PartEventType.RECALLED.value]
    assert len(recall_events) == 1
    assert recall_events[0].from_status == "IN_PROCESS"
    assert recall_events[0].to_status == "PENDING"


# ============================================================
# PROGRAMMING → PENDING
# ============================================================
async def test_recall_programming_to_pending(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=5)
    svc = _make_service(clean_db)

    await svc.send_to_programming(part.id)
    assert part.status == "PROGRAMMING"
    assert part.location == "OFFICE"
    serial_no_before = part.serial_no

    out = await svc.recall_to_pending(part.id)
    assert out.status == "PENDING"
    assert out.location == "OFFICE"
    assert out.current_holder_id is None
    assert out.next_process_id is None
    assert out.serial_no == serial_no_before

    events = await _events(clean_db, part.id)
    recall_events = [e for e in events if e.event_type == PartEventType.RECALLED.value]
    assert len(recall_events) == 1
    assert recall_events[0].from_status == "PROGRAMMING"
    assert recall_events[0].to_status == "PENDING"


# ============================================================
# ON_SHELF → PROGRAMMING
# ============================================================
async def test_recall_on_shelf_to_programming(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=8)
    svc = _make_service(clean_db)

    await _place(svc, part, world)
    serial_no_before = part.serial_no

    out = await svc.recall_to_programming(part.id)
    assert out.status == "PROGRAMMING"
    assert out.location == "OFFICE"
    assert out.current_holder_id is None
    assert out.next_process_id is None
    assert out.serial_no == serial_no_before

    batches = await _batches(clean_db, part.id)
    assert len(batches) == 1
    assert batches[0].status == "PROGRAMMING"
    assert batches[0].location == "OFFICE"
    assert batches[0].next_process_id is None
    assert batches[0].placed_at is None

    events = await _events(clean_db, part.id)
    recall_events = [e for e in events if e.event_type == PartEventType.RECALLED.value]
    assert len(recall_events) == 1
    assert recall_events[0].from_status == "IN_PROCESS"
    assert recall_events[0].to_status == "PROGRAMMING"


# ============================================================
# recall_to_programming 拒绝 PROGRAMMING 状态（已是目标态）
# ============================================================
async def test_recall_to_programming_rejects_programming_batch(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=5)
    svc = _make_service(clean_db)

    await svc.send_to_programming(part.id)
    assert part.status == "PROGRAMMING"

    with pytest.raises(BizError) as ei:
        await svc.recall_to_programming(part.id)
    assert ei.value.code == ErrCode.BIZ_INVALID_TRANSITION


# ============================================================
# WITH_WORKER 批次 → recall_to_pending 拒绝
# ============================================================
async def test_recall_wrong_state_rejected(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=20)
    svc = _make_service(clean_db)

    await _place(svc, part, world)
    # 部分领取：拆 5 给工人
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
        quantity=5,
    ))
    batches = await _batches(clean_db, part.id)
    on_shelf = [b for b in batches if b.location == "PRODUCTION_SHELF"]
    with_worker = [b for b in batches if b.location == "WORKER"]
    assert len(on_shelf) == 1
    assert len(with_worker) == 1

    # 召回在架批次（唯一 ON_SHELF）→ 工人持有批次不动
    out = await svc.recall_to_pending(part.id, batch_id=on_shelf[0].id)
    assert out.status == "PENDING"
    batches = await _batches(clean_db, part.id)
    on_shelf_after = [b for b in batches if b.location == "PRODUCTION_SHELF"]
    with_worker_after = [b for b in batches if b.location == "WORKER"]
    assert len(on_shelf_after) == 0
    assert len(with_worker_after) == 1  # 工人持有批次不动
    assert with_worker_after[0].current_holder_id == world["worker"].id

    # 现在唯一批次是 WITH_WORKER；不指定 batch_id → expect 过滤掉（0 候选）
    # → BIZ_INVALID_TRANSITION
    with pytest.raises(BizError) as ei:
        await svc.recall_to_pending(part.id)
    assert ei.value.code == ErrCode.BIZ_INVALID_TRANSITION

    # 同样对 recall_to_programming：唯一批 WORKER 不匹配 expect → 拒绝
    with pytest.raises(BizError) as ei2:
        await svc.recall_to_programming(part.id)
    assert ei2.value.code == ErrCode.BIZ_INVALID_TRANSITION


# ============================================================
# 多在架批次不带 batch_id → BIZ_INVALID_VALUE
# ============================================================
async def test_recall_multi_batch_requires_batch_id(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=100)
    svc = _make_service(clean_db)

    await _place(svc, part, world)
    # 拆 30 给另一个架工序（仍然 ON_SHELF）—— 这里拆完后源批次留 70，新批次 30，
    # 两批 ON_SHELF + 同工序 + holder 同样。recall_to_pending expect 匹配 2 个。
    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=30)
    batches = await _batches(clean_db, part.id)
    on_shelf = [b for b in batches if b.location == "PRODUCTION_SHELF"]
    assert len(on_shelf) == 2

    # 不带 batch_id → BIZ_INVALID_VALUE（_resolve_target_batch >1 候选）
    with pytest.raises(BizError) as ei:
        await svc.recall_to_pending(part.id)
    assert ei.value.code == ErrCode.BIZ_INVALID_VALUE

    # 带 batch_id → 只召回指定批次；工单 rollup 选最落后批次
    target = on_shelf[0]
    out = await svc.recall_to_pending(part.id, batch_id=target.id)
    assert out.id == part.id
    batches = await _batches(clean_db, part.id)
    pending_batches = [b for b in batches if b.status == "PENDING"]
    remaining_on_shelf = [b for b in batches if b.location == "PRODUCTION_SHELF"]
    assert len(pending_batches) == 1
    assert pending_batches[0].id == target.id
    # rollup：PENDING 进度序 < IN_PROCESS → 工单显示 PENDING
    assert len(remaining_on_shelf) == 1
    assert out.status == "PENDING"


# ============================================================
# 召回保留 serial_no 不释放
# ============================================================
async def test_recall_preserves_serial_no(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=10, serial="R-PRES")
    svc = _make_service(clean_db)
    serial_before = part.serial_no
    assert serial_before is not None

    # ON_SHELF → PENDING 召回
    await _place(svc, part, world)
    out1 = await svc.recall_to_pending(part.id)
    assert out1.serial_no == serial_before

    # PENDING → ON_SHELF → PROGRAMMING 召回（serial 继续保留）
    await _place(svc, part, world)
    out2 = await svc.recall_to_programming(part.id)
    assert out2.serial_no == serial_before

    # PROGRAMMING → PENDING 召回
    out3 = await svc.recall_to_pending(part.id)
    assert out3.serial_no == serial_before


# ============================================================
# recall_to_pending 期望过滤：PROGRAMMING 召回允许，INSPECTION 不允许
# ============================================================
async def test_recall_to_pending_rejects_inspection(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=10)
    svc = _make_service(clean_db)

    # 手动把批次改为 INSPECTION（绕过常规流程）
    root = (await _batches(clean_db, part.id))[0]
    root.status = "INSPECTION"
    root.location = "INSPECTION_SHELF"
    await clean_db.flush()

    with pytest.raises(BizError) as ei:
        await svc.recall_to_pending(part.id)
    assert ei.value.code == ErrCode.BIZ_INVALID_TRANSITION