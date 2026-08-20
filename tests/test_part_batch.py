"""部分数量批次化（2026-07-29）集成测试。

走真实 DB（tests/conftest.py::clean_db + docker 容器），覆盖：
- 拆分守恒 / 边界 / 终态保护
- 部分领取 / 归还 / 品检通过 / 打回 / 发货 全链路
- rollup：最落后状态 / 全部终态 → COMPLETED + serial 释放
- cancel 级联 / 单批次取消
- update_part 总量保护
- 送货单部分量入单（自动拆）+ pickup 批次送货
- 手动 split / cancel_batch
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

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
from model.enums import PartEventType, PartStatus, ShelfZone
from repository.customer import CustomerRepository
from repository.assembly import AssemblyRepository
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
    FailInspectionRequest,
    PartListQuery,
    PartPickUpRequest,
    PartScanRequest,
    PlaceOnShelfRequest,
    PartUpdateRequest,
)
from service.part import PartService
from tests.conftest import seed_root_batch

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers
# ============================================================
async def _make_world(session, *, prefix: str = "B"):
    """客户 + 货架 + 工序 + 映射 + 工人。"""
    cust = TCustomer(name=f"批次测试客户-{prefix}", parent_id=None)
    cust.serial_prefix = prefix
    shelf = TShelf(code=f"PB-{prefix}1", name="生产架", zone=ShelfZone.PRODUCTION.value)
    insp_shelf = TShelf(code=f"PB-{prefix}2", name="品检架", zone=ShelfZone.INSPECTION.value)
    process = TProcess(code=f"PROC-{prefix}1", name="工序1", category="INHOUSE", sort_order=0)
    process2 = TProcess(code=f"PROC-{prefix}2", name="工序2", category="INHOUSE", sort_order=1)
    wt = TWorkType(code=f"WT-{prefix}", name="工种")
    worker = TWorker(
        badge_code=f"BDG-{prefix}", name="批次工人", is_active=True,
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
        assemblies=AssemblyRepository(session),
        broadcaster=None,
        event_broadcaster=None,
    )


async def _make_part(
    session, customer: TCustomer, *, qty: int = 100, serial: str = "B0001",
    status: str = "PENDING", location: str = "OFFICE",
) -> TPart:
    part = TPart(
        serial_no=serial,
        name=f"批次件-{serial}",
        drawing_no=f"DWG-{serial}",
        applicant_name="批次申请人",
        quantity=qty,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        customer_id=customer.id,
        status=status,
        location=location,
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


async def _place(session, svc, part, world, qty=None):
    return await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world["shelf"].id,
            next_process_id=world["process"].id,
            quantity=qty,
        ),
    )


# ============================================================
# 拆分守恒与边界
# ============================================================
async def test_split_conserves_quantity(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=100)
    svc = _make_service(clean_db)

    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=30)

    batches = await _batches(clean_db, part.id)
    assert len(batches) == 2
    assert sum(b.quantity for b in batches) == 100
    assert batches[0].quantity == 70
    assert batches[1].quantity == 30
    assert batches[1].parent_batch_id == root.id
    # SPLIT 事件挂在新批次上
    events = await _events(clean_db, part.id)
    split_events = [e for e in events if e.event_type == PartEventType.SPLIT.value]
    assert len(split_events) == 1
    assert split_events[0].batch_id == batches[1].id
    assert split_events[0].quantity == 30


async def test_split_rejects_bad_quantity(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=10)
    svc = _make_service(clean_db)
    root = (await _batches(clean_db, part.id))[0]

    for bad in (0, -1, 10, 11):
        with pytest.raises(BizError):
            await svc.split_batch(part.id, batch_id=root.id, quantity=bad)


async def test_split_rejects_terminal_batch(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=10)
    svc = _make_service(clean_db)
    await svc.cancel(part.id)
    root = (await _batches(clean_db, part.id))[0]
    with pytest.raises(BizError):
        await svc.split_batch(part.id, batch_id=root.id, quantity=1)


# ============================================================
# 部分领取 / 归还（扫码台）
# ============================================================
async def test_partial_pick_up_and_return(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=100)
    svc = _make_service(clean_db)
    await _place(clean_db, svc, part, world)
    assert part.status == "IN_PROCESS"

    # 领 30：拆 30 给工人，70 留架
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
        quantity=30,
    ))
    batches = await _batches(clean_db, part.id)
    assert len(batches) == 2
    on_shelf = [b for b in batches if b.location == "PRODUCTION_SHELF"]
    with_worker = [b for b in batches if b.location == "WORKER"]
    assert on_shelf[0].quantity == 70
    assert with_worker[0].quantity == 30
    assert with_worker[0].current_holder_id == world["worker"].id

    # 事件：PICKED_UP 带 batch_id + quantity=30
    events = await _events(clean_db, part.id)
    pick_events = [e for e in events if e.event_type == PartEventType.PICKED_UP.value]
    assert pick_events[-1].quantity == 30
    assert pick_events[-1].batch_id == with_worker[0].id

    # 归还 10：工人手里 30 → 拆 10 回架（新批次），剩 20
    await svc.scan_event(PartScanRequest(
        serial_no=part.serial_no,
        event_type=PartEventType.RETURNED,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
        next_process_id=world["process2"].id,
        quantity=10,
    ))
    batches = await _batches(clean_db, part.id)
    assert len(batches) == 3
    assert sum(b.quantity for b in batches) == 100
    worker_left = [b for b in batches if b.location == "WORKER"]
    shelf_batches = [b for b in batches if b.location == "PRODUCTION_SHELF"]
    assert worker_left[0].quantity == 20
    assert sorted(b.quantity for b in shelf_batches) == [10, 70]
    returned = [b for b in shelf_batches if b.quantity == 10][0]
    assert returned.next_process_id == world["process2"].id


# ============================================================
# 部分品检（通过 / 打回）
# ============================================================
async def test_partial_inspection_pass_and_fail(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=20)
    svc = _make_service(clean_db)
    await _place(clean_db, svc, part, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    await svc.scan_event(PartScanRequest(
        serial_no=part.serial_no,
        event_type=PartEventType.INSPECTED,
        shelf_id=world["insp_shelf"].id,
        badge_code=world["worker"].badge_code,
        target_inspection_shelf_id=world["insp_shelf"].id,
    ))
    assert part.status == "INSPECTION"

    # 通过 15
    await svc.pass_inspection(part.id, quantity=15)
    # 打回 5
    await svc.fail_inspection(
        part.id,
        FailInspectionRequest(
            shelf_id=str(world["shelf"].id),
            next_process_id=str(world["process"].id),
            note="5件尺寸超差",
        ),
    )
    batches = await _batches(clean_db, part.id)
    assert sum(b.quantity for b in batches) == 20
    by_status = {}
    for b in batches:
        by_status.setdefault(b.status, []).append(b.quantity)
    assert by_status["READY_TO_SHIP"] == [15]
    assert by_status["IN_PROCESS"] == [5]
    # rollup：最落后 = IN_PROCESS
    assert part.status == "IN_PROCESS"


# ============================================================
# 部分发货 + rollup 终态 + serial 释放
# ============================================================
async def test_partial_deliver_and_full_completion(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=20)
    svc = _make_service(clean_db)
    await _place(clean_db, svc, part, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    await svc.scan_event(PartScanRequest(
        serial_no=part.serial_no,
        event_type=PartEventType.INSPECTED,
        shelf_id=world["insp_shelf"].id,
        badge_code=world["worker"].badge_code,
        target_inspection_shelf_id=world["insp_shelf"].id,
    ))
    await svc.pass_inspection(part.id)

    # 部分发货 12
    await svc.deliver(part.id, quantity=12)
    batches = await _batches(clean_db, part.id)
    assert sorted(b.quantity for b in batches) == [8, 12]
    assert part.status == "READY_TO_SHIP"  # 最落后
    assert part.actual_delivery_date is None  # 未全部送出
    assert part.serial_no is not None  # serial 未释放

    # 剩余 8 发货 → 全部 DELIVERED
    await svc.deliver(part.id)
    assert part.status == "DELIVERED"
    assert part.actual_delivery_date is not None

    # complete：默认完成全部 DELIVERED 批次 → 工单 COMPLETED + serial 释放
    await svc.complete(part.id)
    assert part.status == "COMPLETED"
    assert part.serial_no is None
    batches = await _batches(clean_db, part.id)
    assert all(b.status == "COMPLETED" for b in batches)
    # 工单级 rollup 事件（batch_id=NULL）
    events = await _events(clean_db, part.id)
    completed = [e for e in events if e.event_type == PartEventType.COMPLETED.value]
    assert any(e.batch_id is None for e in completed)


# ============================================================
# cancel：级联 / 单批次
# ============================================================
async def test_cancel_single_batch_keeps_part_active(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)
    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=20)

    batches = await _batches(clean_db, part.id)
    victim = batches[1]
    await svc.cancel_batch(part.id, batch_id=victim.id)

    batches = await _batches(clean_db, part.id)
    assert batches[1].status == "CANCELLED"
    assert batches[0].status == "PENDING"
    assert part.status == "PENDING"
    assert part.serial_no is not None
    # Σ 不变量保持（CANCELLED 批次保留数量）
    assert sum(b.quantity for b in batches) == 50


async def test_cancel_cascades_and_releases_serial(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)
    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=20)

    await svc.cancel(part.id)
    batches = await _batches(clean_db, part.id)
    assert all(b.status == "CANCELLED" for b in batches)
    assert part.status == "CANCELLED"
    assert part.serial_no is None


# ============================================================
# update_part 总量保护
# ============================================================
async def test_update_quantity_locked_after_split(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)
    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=10)

    with pytest.raises(BizError):
        await svc.update_part(part.id, PartUpdateRequest(quantity=80))


async def test_update_quantity_allowed_on_pending_root(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)

    await svc.update_part(part.id, PartUpdateRequest(quantity=80))
    assert part.quantity == 80
    root = (await _batches(clean_db, part.id))[0]
    assert root.quantity == 80  # 根批次同步，Σ 守恒


async def test_update_quantity_locked_after_place_on_shelf(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)
    await _place(clean_db, svc, part, world)

    with pytest.raises(BizError):
        await svc.update_part(part.id, PartUpdateRequest(quantity=80))


# ============================================================
# 送货单：部分量入单（自动拆）+ pickup 批次送货
# ============================================================
async def test_delivery_note_partial_add_and_pickup(clean_db):
    from repository.delivery_note import (
        DeliveryNoteCounterRepository,
        DeliveryNoteEventRepository,
        DeliveryNoteRepository,
    )
    from service.delivery_note import DeliveryNoteService
    from schema.delivery_note import DeliveryNoteAddPartsItem

    world = await _make_world(clean_db)
    # 司机
    driver_wt = TWorkType(code="送货司机", name="送货司机")
    clean_db.add(driver_wt)
    await clean_db.flush()
    driver = TWorker(
        badge_code="DRV-1", name="司机", is_active=True,
        work_type_id=driver_wt.id,
    )
    clean_db.add(driver)
    await clean_db.flush()

    part = await _make_part(
        clean_db, world["customer"], qty=30,
        status="READY_TO_SHIP", location=None,
    )
    svc = _make_service(clean_db)
    note_svc = DeliveryNoteService(
        session=clean_db,
        notes=DeliveryNoteRepository(clean_db),
        note_events=DeliveryNoteEventRepository(clean_db),
        counter=DeliveryNoteCounterRepository(clean_db),
        parts=PartRepository(clean_db),
        part_batches=PartBatchRepository(clean_db),
        customers=CustomerRepository(clean_db),
        workers=WorkerRepository(clean_db),
        work_types=WorkTypeRepository(clean_db),
        part_events=PartEventRepository(clean_db),
        current_user=None,
    )

    root = (await _batches(clean_db, part.id))[0]
    note = await note_svc.create_draft(customer_id=str(world["customer"].id))
    # 入单 12 件（批次 30 → 自动拆 12/18）
    detail = await note_svc.add_parts(
        note_id=str(note.id),
        items=[DeliveryNoteAddPartsItem(batch_id=str(root.id), quantity=12)],
        version=note.version,
    )
    assert len(detail.line_items) == 1
    assert detail.line_items[0].quantity == 12

    batches = await _batches(clean_db, part.id)
    assert sorted(b.quantity for b in batches) == [12, 18]
    on_note = [b for b in batches if b.delivery_note_id is not None]
    assert on_note[0].quantity == 12

    submitted = await note_svc.submit(note_id=str(note.id), version=detail.version)
    assert submitted.status == "SUBMITTED"

    # pickup：批次 DELIVERED；工单留 READY_TO_SHIP（还有 18 件待发）
    picked = await note_svc.pickup(
        note_id=str(note.id),
        driver_worker_id=str(driver.id),
        version=submitted.version,
    )
    assert picked.status == "PICKED_UP"
    batches = await _batches(clean_db, part.id)
    delivered = [b for b in batches if b.status == "DELIVERED"]
    assert len(delivered) == 1
    assert delivered[0].quantity == 12
    assert part.status == "READY_TO_SHIP"


# ============================================================
# 批次解析：多批次必须指定 batch_id
# ============================================================
async def test_ambiguous_batches_require_batch_id(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)
    # 拆出两个 PENDING 批次（root 50 → 30 + 20）
    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=20)

    with pytest.raises(BizError) as exc:
        await _place(clean_db, svc, part, world)
    assert "batch_id" in str(exc.value)

    # 指定 batch_id 可下发
    batches = await _batches(clean_db, part.id)
    await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world["shelf"].id,
            next_process_id=world["process"].id,
            batch_id=str(batches[0].id),
        ),
    )
    batches = await _batches(clean_db, part.id)
    assert batches[0].status == "IN_PROCESS"
    assert batches[1].status == "PENDING"
    assert part.status == "PENDING"  # 最落后



# ============================================================
# 批次化外协可发送一览（2026-07-29 PR-fix-0.2.0 回归）
# ============================================================
async def test_outsource_sendable_lists_qualifying_batch_in_multibatch_order(clean_db):
    """批次化后外协可发送候选必须按批次过滤。

    场景：工单拆为两个批次，一个 PENDING 在 OFFICE，一个 IN_PROCESS + PRODUCTION_SHELF
    在绑了 OUTSOURCE 工序的货架上。工单 rollup 仍是 PENDING（最落后），旧版候选查询
    因此把整张工单过滤掉。修复后必须返回可外发的那个批次。
    """
    from model import TShelfProcess
    from model.enums import ProcessCategory, PartLocation

    # 1. 建世界（OUTSOURCE 工序 + C2 货架 + 货架↔工序映射）
    # _make_world 用 prefix 当 serial_prefix，必须 1 字符；这里用 "O"（外协）。
    world = await _make_world(clean_db, prefix="O")
    outsource_process = TProcess(
        code=f"OUT-O-1", name="外协工序1",
        category=ProcessCategory.OUTSOURCE.value, sort_order=10,
    )
    clean_db.add(outsource_process)
    await clean_db.flush()
    # 在原生产架 (world["shelf"]) 上加 OUTSOURCE 映射 → 该货架绑了外协工序
    clean_db.add(TShelfProcess(
        shelf_id=world["shelf"].id, process_id=outsource_process.id, sort_order=10,
    ))
    await clean_db.flush()

    # 2. 建工单（qty=50 → 根批次 qty=50）
    part = await _make_part(clean_db, world["customer"], qty=50, serial="OB0001")
    svc = _make_service(clean_db)

    # 3. 拆：50 → 30 (PENDING) + 20 (IN_PROCESS 可外发)
    root = (await _batches(clean_db, part.id))[0]
    await svc.split_batch(part.id, batch_id=root.id, quantity=20)

    # 4. place_on_shelf：把批次 1 (qty=20) 放到 OUTSOURCE 货架上
    batches = await _batches(clean_db, part.id)
    # 找出 qty=20 的批次
    target_batch = next(b for b in batches if b.quantity == 20)
    await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world["shelf"].id,
            next_process_id=outsource_process.id,
            batch_id=str(target_batch.id),
        ),
    )

    # 5. 重新读：rollup 应该是 PENDING（最落后）
    part = await PartRepository(clean_db).get_by_id(part.id)
    assert part.status == PartStatus.PENDING.value, (
        "工单 rollup 应为 PENDING（批次 2 还在 PENDING）"
    )
    batches = await _batches(clean_db, part.id)
    assert any(b.status == PartStatus.PENDING.value for b in batches)
    assert any(b.status == PartStatus.IN_PROCESS.value for b in batches)

    # 6. 调用 list_outsource_sendable —— 应只返回那个可外发的批次
    parts_repo = PartRepository(clean_db)
    rows = await parts_repo.list_outsource_sendable(limit=10, offset=0)
    assert len(rows) == 1, (
        f"PR-fix-0.2.0 回归失败：期望 1 行（批次 2），实际 {len(rows)} 行"
        "——多批次工单的可外发批次被 rollup 过滤掉了"
    )
    batch, p = rows[0]
    assert p.id == part.id
    assert batch.quantity == 20
    assert batch.status == PartStatus.IN_PROCESS.value
    assert batch.location == PartLocation.PRODUCTION_SHELF.value

    # 7. count 也应只数 1 行（按批次计）
    count = await parts_repo.count_outsource_sendable()
    assert count == 1


# ============================================================
# 2026-08-20：列表「已送数量」列（PartListItem.delivered_quantity）
# ============================================================
async def test_list_parts_delivered_quantity_includes_partial_full_and_completed(clean_db):
    """列表已送数量 = 未软删批次中 status ∈ (DELIVERED, COMPLETED) 的 quantity 之和。

    覆盖：
    - 部分量 deliver 12/20 → 列表 delivered_quantity == 12
    - 全部 deliver 20/20 → 列表 delivered_quantity == 20
    - 自动 COMPLETED 后 → 列表 delivered_quantity == 20（COMPLETED 仍计入）
    """
    from model import TAssembly
    from sqlalchemy import select

    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=20, serial="B9001")
    svc = _make_service(clean_db)

    # 全流程推到 READY_TO_SHIP
    await _place(clean_db, svc, part, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    await svc.scan_event(PartScanRequest(
        serial_no=part.serial_no,
        event_type=PartEventType.INSPECTED,
        shelf_id=world["insp_shelf"].id,
        badge_code=world["worker"].badge_code,
        target_inspection_shelf_id=world["insp_shelf"].id,
    ))
    await svc.pass_inspection(part.id)

    # 部分发货 12
    await svc.deliver(part.id, quantity=12)

    async def _list_q():
        return await svc.list_parts(PartListQuery(limit=100))

    out = await _list_q()
    items = {i.serial_no: i for i in out.items}
    assert items[part.serial_no].delivered_quantity == 12

    # 剩余 8 发完 → 全部 DELIVERED
    await svc.deliver(part.id)

    # 校验 DB 状态（防 helper 顺序漂移）
    delivered_batches = (
        await clean_db.execute(
            select(TPartBatch).where(
                TPartBatch.part_id == part.id,
                TPartBatch.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    assert all(
        b.status in (
            PartStatus.DELIVERED.value,
            PartStatus.COMPLETED.value,
        )
        for b in delivered_batches
    )

    out = await _list_q()
    items = {i.serial_no: i for i in out.items}
    assert items[part.serial_no].delivered_quantity == 20

    # complete：DELIVERED → COMPLETED，已送量仍计入
    await svc.complete(part.id)
    out = await _list_q()
    items = {i.serial_no: i for i in out.items}
    assert items[part.serial_no].delivered_quantity == 20


async def test_list_parts_assembly_delivered_quantity_is_null(clean_db):
    """装配件行走 _assemblies_to_list_items → PartListItem.delivered_quantity 恒为 None。"""
    from model import TAssembly

    world = await _make_world(clean_db)
    asm = TAssembly(
        drawing_no="ASM-DQ-001",
        name="已送数量-装配体",
        applicant_name="批次申请人",
        customer_id=world["customer"].id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        status="PENDING",
        serial_no="A9001",
        quantity=1,
        unit_price=0,
        total_price=0,
    )
    asm.created_by = None
    asm.updated_by = None
    clean_db.add(asm)
    await clean_db.flush()

    svc = _make_service(clean_db)
    out = await svc.list_parts(
        PartListQuery(include_assemblies=True, limit=100),
    )
    asm_items = [i for i in out.items if i.row_type == "ASSEMBLY"]
    assert len(asm_items) == 1
    assert asm_items[0].delivered_quantity is None
