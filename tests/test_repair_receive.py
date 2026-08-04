"""「返修接收」PR-M (2026-08-04) 集成测试。

覆盖：
- start_repair 写 has_been_repaired = True（工单 + 批次）
- complete_repair (PRODUCTION 区) → ON_SHELF，has_been_repaired 保持 True
- complete_repair (INSPECTION 区) → INSPECTION (新 transition)
- shelf.zone 非法 → BIZ_INVALID_VALUE
- shelf↔process 校验：PRODUCTION 区未映射 → BIZ_SHELF_PROCESS_NOT_MAPPED
- 部分量 start_repair 先拆再返修
- list_repair_batches / list_repairing_batches 端点
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
    TWorkType,
)
from model.enums import PartEventType, PartStatus, ShelfZone
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
from service.part import PartService
from tests.conftest import seed_root_batch

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers
# ============================================================
async def _make_world(session, *, prefix: str = "R"):
    """客户 + 生产/品检 货架 + 两工序 + 工种。"""
    cust = TCustomer(name=f"返修测试-{prefix}", parent_id=None)
    cust.serial_prefix = prefix
    prod_shelf = TShelf(
        code=f"PR-{prefix}", name="返修生产架", zone=ShelfZone.PRODUCTION.value,
    )
    insp_shelf = TShelf(
        code=f"IN-{prefix}", name="返修品检架", zone=ShelfZone.INSPECTION.value,
    )
    proc_a = TProcess(
        code=f"PRA-{prefix}", name="返修工序A", category="INHOUSE", sort_order=0,
    )
    proc_b = TProcess(
        code=f"PRB-{prefix}", name="返修工序B", category="INHOUSE", sort_order=1,
    )
    wt = TWorkType(code=f"WTR-{prefix}", name="返修工种")
    session.add_all([cust, prod_shelf, insp_shelf, proc_a, proc_b, wt])
    await session.flush()
    session.add(TShelfProcess(shelf_id=prod_shelf.id, process_id=proc_a.id, sort_order=0))
    session.add(TShelfProcess(shelf_id=prod_shelf.id, process_id=proc_b.id, sort_order=1))
    await session.flush()
    return {
        "customer": cust,
        "prod_shelf": prod_shelf,
        "insp_shelf": insp_shelf,
        "proc_a": proc_a,
        "proc_b": proc_b,
        "wt": wt,
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
    session,
    customer: TCustomer,
    *,
    qty: int = 10,
    serial: str = "R0001",
    status: str = "DELIVERED",
    next_process_id: int | None = None,
) -> TPart:
    part = TPart(
        serial_no=serial,
        name=f"返修件-{serial}",
        drawing_no=f"DWG-{serial}",
        applicant_name="返修申请人",
        quantity=qty,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 8, 1),
        customer_id=customer.id,
        status=status,
        location=None,
        next_process_id=next_process_id,
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


# ============================================================
# has_been_repaired 持久化
# ============================================================
async def test_start_repair_marks_has_been_repaired(clean_db):
    """start_repair 后工单 + 当前批次都打上 has_been_repaired = True"""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], serial="R0101", status="DELIVERED",
        next_process_id=world["proc_a"].id,
    )
    svc = _make_service(clean_db)

    out = await svc.start_repair(part.id)
    assert out.status == PartStatus.REPAIRING
    assert out.has_been_repaired is True

    # re-fetch 工单与批次行验证 DB
    refreshed = await PartRepository(clean_db).get_by_id(part.id)
    assert refreshed.has_been_repaired is True

    batches = await _batches(clean_db, part.id)
    assert len(batches) == 1
    assert batches[0].has_been_repaired is True

    # 事件流包含 REPAIR_STARTED
    events = await _events(clean_db, part.id)
    assert any(e.event_type == PartEventType.REPAIR_STARTED.value for e in events)


# ============================================================
# complete_repair (PRODUCTION)
# ============================================================
async def test_complete_repair_to_production(clean_db):
    """REPAIRING → ON_SHELF：delivered -> start_repair -> complete_repair(PRODUCTION) -> IN_PROCESS on shelf."""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], serial="R0201", status="DELIVERED",
        next_process_id=world["proc_a"].id,
    )
    svc = _make_service(clean_db)

    await svc.start_repair(part.id)

    out = await svc.complete_repair(
        part.id, shelf_id=world["prod_shelf"].id, batch_id=None,
    )
    assert out.status == PartStatus.IN_PROCESS
    assert out.has_been_repaired is True  # 标记贯穿

    # 批次落到货架
    batches = await _batches(clean_db, part.id)
    assert batches[0].status == "IN_PROCESS"
    assert batches[0].location == "PRODUCTION_SHELF"
    assert batches[0].current_holder_id == world["prod_shelf"].id


async def test_complete_repair_overrides_next_process_id(clean_db):
    """caller 显式 next_process_id 覆盖 carried."""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], serial="R0202", status="DELIVERED",
        next_process_id=world["proc_a"].id,
    )
    svc = _make_service(clean_db)
    await svc.start_repair(part.id)

    # 传 proc_b 替代 carried proc_a
    out = await svc.complete_repair(
        part.id, shelf_id=world["prod_shelf"].id,
        next_process_id=world["proc_b"].id,
    )
    assert out.next_process_id == world["proc_b"].id


async def test_complete_repair_rejects_unmapped_process(clean_db):
    """shelf↔process 校验：货架未映射 carried process → BIZ_SHELF_PROCESS_NOT_MAPPED."""
    world = await _make_world(clean_db)
    # 单独建一个未映射 PROC-C 的货架
    unmapped_shelf = TShelf(
        code=f"UM-{world['proc_b'].id}",
        name="未映射架", zone=ShelfZone.PRODUCTION.value,
    )
    clean_db.add(unmapped_shelf)
    await clean_db.flush()

    part = await _make_part(
        clean_db, world["customer"], serial="R0203", status="DELIVERED",
        next_process_id=world["proc_a"].id,  # carried 是 proc_a
    )
    svc = _make_service(clean_db)
    await svc.start_repair(part.id)

    with pytest.raises(BizError) as exc:
        await svc.complete_repair(
            part.id, shelf_id=unmapped_shelf.id,
            next_process_id=world["proc_b"].id,  # 该架未映射 proc_b
        )
    assert exc.value.code == ErrCode.BIZ_SHELF_PROCESS_NOT_MAPPED


# ============================================================
# complete_repair (INSPECTION) — 新 transition
# ============================================================
async def test_complete_repair_to_inspection(clean_db):
    """REPAIRING → INSPECTION：走新 transition；on_enter_INSPECTION 既定 location/holder 行为。"""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], serial="R0301", status="DELIVERED",
    )
    svc = _make_service(clean_db)
    await svc.start_repair(part.id)

    out = await svc.complete_repair(part.id, shelf_id=world["insp_shelf"].id)
    assert out.status == PartStatus.INSPECTION

    batches = await _batches(clean_db, part.id)
    assert batches[0].status == "INSPECTION"
    assert batches[0].location == "INSPECTION_SHELF"
    assert batches[0].current_holder_id == world["insp_shelf"].id

    # 标记依然 True
    refreshed = await PartRepository(clean_db).get_by_id(part.id)
    assert refreshed.has_been_repaired is True


async def test_complete_repair_rejects_inactive_shelf(clean_db):
    """shelf.is_active=False → BIZ_SHELF_IN_USE."""
    world = await _make_world(clean_db)
    inactive_shelf = TShelf(
        code="INA-R", name="已停用生产架", zone=ShelfZone.PRODUCTION.value,
        is_active=False,
    )
    clean_db.add(inactive_shelf)
    await clean_db.flush()
    # 给该架加一个映射（避免其它校验先失败）
    clean_db.add(TShelfProcess(
        shelf_id=inactive_shelf.id, process_id=world["proc_a"].id, sort_order=0,
    ))
    await clean_db.flush()

    part = await _make_part(
        clean_db, world["customer"], serial="R0302", status="DELIVERED",
        next_process_id=world["proc_a"].id,
    )
    svc = _make_service(clean_db)
    await svc.start_repair(part.id)

    with pytest.raises(BizError) as exc:
        await svc.complete_repair(part.id, shelf_id=inactive_shelf.id)
    assert exc.value.code == ErrCode.BIZ_SHELF_IN_USE


# ============================================================
# 部分量 start_repair 先拆再返修
# ============================================================
async def test_partial_start_repair_splits_then_marks(clean_db):
    """部分量 start_repair: 先拆出 quantity=3 进入 REPAIRING, 剩 7 仍 DELIVERED."""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], qty=10, serial="R0401", status="DELIVERED",
    )
    svc = _make_service(clean_db)

    out = await svc.start_repair(part.id, quantity=3)
    assert out.status == PartStatus.REPAIRING  # 工单 rollup 取 REPAIRING 批

    batches = await _batches(clean_db, part.id)
    assert len(batches) == 2
    # 源头批次 DELIVERED（未标记返修）, 新批次 REPAIRING（已标记返修）
    by_no = {b.batch_no: b for b in batches}
    assert by_no[1].status == "DELIVERED"
    assert by_no[1].has_been_repaired is False
    assert by_no[2].status == "REPAIRING"
    assert by_no[2].has_been_repaired is True
    assert by_no[2].quantity == 3

    # 工单级 has_been_repaired 也被置 True（service 同步）
    refreshed = await PartRepository(clean_db).get_by_id(part.id)
    assert refreshed.has_been_repaired is True


# ============================================================
# list_repair_batches / list_repairing_batches 端点级 service 方法
# ============================================================
async def test_list_repair_batches_returns_only_delivered(clean_db):
    world = await _make_world(clean_db)
    svc = _make_service(clean_db)

    # 2 个 DELIVERED + 1 个 INSPECTION + 1 个 PENDING
    p1 = await _make_part(
        clean_db, world["customer"], serial="R0501", status="DELIVERED",
    )
    p2 = await _make_part(
        clean_db, world["customer"], serial="R0502", status="DELIVERED",
    )
    await _make_part(
        clean_db, world["customer"], serial="R0503", status="INSPECTION",
    )
    await _make_part(
        clean_db, world["customer"], serial="R0504", status="PENDING",
    )

    items, total = await svc.list_repair_batches(limit=50, offset=0)
    serials = {item.serial_no for item in items}
    assert total == 2
    assert serials == {"R0501", "R0502"}


async def test_list_repairing_batches_returns_only_repairing(clean_db):
    world = await _make_world(clean_db)
    svc = _make_service(clean_db)

    p1 = await _make_part(
        clean_db, world["customer"], serial="R0601", status="DELIVERED",
    )
    p2 = await _make_part(
        clean_db, world["customer"], serial="R0602", status="DELIVERED",
    )
    await svc.start_repair(p1.id)
    await svc.start_repair(p2.id)

    items, total = await svc.list_repairing_batches(limit=50, offset=0)
    assert total == 2
    assert all(it.status == PartStatus.REPAIRING for it in items)
    assert all(it.has_been_repaired is True for it in items)


# ============================================================
# 一站式 repair_dispatch（PR-M 2026-08-04 续）
# ============================================================
async def test_repair_dispatch_to_production(clean_db):
    """DELIVERED → REPAIRING → ON_SHELF 一站式：has_been_repaired 同步置 true，事件流含 2 条"""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], serial="R0701", status="DELIVERED",
        next_process_id=world["proc_a"].id,
    )
    svc = _make_service(clean_db)

    out = await svc.repair_dispatch(
        part.id, shelf_id=world["prod_shelf"].id,
    )
    assert out.status == PartStatus.IN_PROCESS
    assert out.has_been_repaired is True

    # 工单级 has_been_repaired 持久化
    refreshed = await PartRepository(clean_db).get_by_id(part.id)
    assert refreshed.has_been_repaired is True

    # 事件流含 REPAIR_STARTED + REPAIR_COMPLETED 两条
    events = await _events(clean_db, part.id)
    types = {e.event_type for e in events}
    assert PartEventType.REPAIR_STARTED.value in types
    assert PartEventType.REPAIR_COMPLETED.value in types


async def test_repair_dispatch_to_inspection(clean_db):
    """DELIVERED → REPAIRING → INSPECTION 一站式：走新 transition，无需 process 校验"""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], serial="R0702", status="DELIVERED",
    )
    svc = _make_service(clean_db)

    out = await svc.repair_dispatch(
        part.id, shelf_id=world["insp_shelf"].id,
    )
    assert out.status == PartStatus.INSPECTION

    batches = await _batches(clean_db, part.id)
    assert batches[0].status == "INSPECTION"
    assert batches[0].location == "INSPECTION_SHELF"
    assert batches[0].current_holder_id == world["insp_shelf"].id
    assert batches[0].has_been_repaired is True


async def test_repair_dispatch_partial_quantity(clean_db):
    """部分量 quantity<batch.quantity：_maybe_split 拆批，新批标 has_been_repaired=True"""
    world = await _make_world(clean_db)
    part = await _make_part(
        clean_db, world["customer"], qty=10, serial="R0703", status="DELIVERED",
    )
    svc = _make_service(clean_db)

    out = await svc.repair_dispatch(
        part.id, shelf_id=world["prod_shelf"].id,
        next_process_id=world["proc_a"].id,
        quantity=3,
    )
    assert out.status == PartStatus.IN_PROCESS

    refreshed = await PartRepository(clean_db).get_by_id(part.id)
    assert refreshed.has_been_repaired is True

    # 源批 7 仍 DELIVERED（未标记返修）；新批 3 REPAIRING → ON_SHELF（已标记）
    batches = await _batches(clean_db, part.id)
    by_no = {b.batch_no: b for b in batches}
    assert by_no[1].status == "DELIVERED"
    assert by_no[1].quantity == 7
    assert by_no[1].has_been_repaired is False
    assert by_no[2].status == "IN_PROCESS"
    assert by_no[2].quantity == 3
    assert by_no[2].has_been_repaired is True
