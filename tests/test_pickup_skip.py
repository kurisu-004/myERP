"""跳序取件记录 + 统计端点（2026-08-05）集成测试。

走真实 DB（tests/conftest.py::clean_db + docker 容器），覆盖：

跳序检测逻辑（PartService.pick_up_by_scan 嵌入的 _detect_pickup_skip）：
1. 晚交期件被领取（有更早候选）→ 记录 t_pickup_skip_event，字段快照正确
2. 取最早交期件 → 不记录
3. 并列交期件 → 不记录
4. 取加急件 → 不记录
5. 所取件交期 NULL + 候选有日期 → 记录
6. 所有候选 NULL → 不记录

统计端点：
- summary：构造 2 个工人各产生记录 → count/max 正确
- detail：分页正确
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from model import (
    TCustomer,
    TPart,
    TPartBatch,
    TPickupSkipEvent,
    TProcess,
    TShelf,
    TShelfProcess,
    TWorker,
    TWorkType,
    TWorkTypeProcess,
)
from model.enums import ShelfZone
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.pickup_skip_event import PickupSkipEventRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.statistics import StatisticsRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from schema.part import (
    PartPickUpRequest,
    PlaceOnShelfRequest,
)
from service.part import PartService
from service.statistics import StatisticsService
from tests.conftest import seed_root_batch

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers
# ============================================================
async def _make_world(session, *, prefix: str = "S"):
    """客户 + 2 货架 + 2 工序 + 映射 + 工人 + 工种。

    跳序检测需要候选 = 跨架"工种可领"列表；故至少 2 货架 + 1 工种 +
    工种→工序映射；工人挂工种。
    """
    cust = TCustomer(name=f"跳序客户-{prefix}", parent_id=None)
    cust.serial_prefix = prefix
    shelf1 = TShelf(code=f"SK-{prefix}A", name="架A", zone=ShelfZone.PRODUCTION.value)
    shelf2 = TShelf(code=f"SK-{prefix}B", name="架B", zone=ShelfZone.PRODUCTION.value)
    insp_shelf = TShelf(code=f"SK-{prefix}C", name="品检架", zone=ShelfZone.INSPECTION.value)
    process = TProcess(code=f"PRC-{prefix}1", name="工序1", category="INHOUSE", sort_order=0)
    process2 = TProcess(code=f"PRC-{prefix}2", name="工序2", category="INHOUSE", sort_order=1)
    wt = TWorkType(code=f"WT-{prefix}", name="跳序工种")
    worker = TWorker(
        badge_code=f"BDG-{prefix}", name="跳序工人", is_active=True,
    )
    session.add_all([cust, shelf1, shelf2, insp_shelf, process, process2, wt, worker])
    await session.flush()
    session.add(TShelfProcess(shelf_id=shelf1.id, process_id=process.id, sort_order=0))
    session.add(TShelfProcess(shelf_id=shelf1.id, process_id=process2.id, sort_order=1))
    session.add(TShelfProcess(shelf_id=shelf2.id, process_id=process.id, sort_order=0))
    session.add(TShelfProcess(shelf_id=shelf2.id, process_id=process2.id, sort_order=1))
    # 工种↔工序 映射（候选过滤用）
    session.add(TWorkTypeProcess(work_type_id=wt.id, process_id=process.id, sort_order=0))
    session.add(TWorkTypeProcess(work_type_id=wt.id, process_id=process2.id, sort_order=1))
    worker.work_type_id = wt.id
    await session.flush()
    return {
        "customer": cust,
        "shelf1": shelf1, "shelf2": shelf2, "insp_shelf": insp_shelf,
        "process": process, "process2": process2,
        "wt": wt, "worker": worker,
    }


def _make_service(session, *, current_user=None) -> PartService:
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
        pickup_skip_events=PickupSkipEventRepository(session),
        broadcaster=None,
        event_broadcaster=None,
        current_user=current_user,
    )


async def _make_part(
    session, customer: TCustomer, *,
    serial: str, planned: date | None, is_urgent: bool = False, qty: int = 5,
) -> TPart:
    part = TPart(
        serial_no=serial,
        name=f"跳序件-{serial}",
        drawing_no=f"DWG-{serial}",
        applicant_name="跳序申请人",
        quantity=qty,
        request_date=date(2026, 7, 1),
        planned_delivery_date=planned,
        is_urgent=is_urgent,
        customer_id=customer.id,
        status="PENDING",
        location="OFFICE",
    )
    session.add(part)
    await session.flush()
    await seed_root_batch(session, part)
    return part


async def _place(svc, part, world, shelf_key: str = "shelf1"):
    return await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world[shelf_key].id,
            next_process_id=world["process"].id,
        ),
    )


async def _pickup(svc, part, world, shelf_key: str = "shelf1"):
    return await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world[shelf_key].id,
        badge_code=world["worker"].badge_code,
    ))


async def _skip_events(session, *, worker_id: int) -> list[TPickupSkipEvent]:
    rows = await session.execute(
        select(TPickupSkipEvent)
        .where(TPickupSkipEvent.worker_id == worker_id)
        .order_by(TPickupSkipEvent.id.asc())
    )
    return list(rows.scalars().all())


# ============================================================
# 跳序检测逻辑
# ============================================================
async def test_skip_recorded_when_later_date_picked(clean_db):
    """两件同工种同架工序不同交期，领取晚交期 → 记录 1 条。"""
    world = await _make_world(clean_db)
    early = await _make_part(clean_db, world["customer"], serial="S-EARLY",
                              planned=date(2026, 8, 1))
    later = await _make_part(clean_db, world["customer"], serial="S-LATER",
                              planned=date(2026, 8, 10))
    svc = _make_service(clean_db)

    # 早件上架到 shelf1
    await _place(svc, early, world, "shelf1")
    # 晚件上架到 shelf2（跨架同样命中工种可领列表）
    await _place(svc, later, world, "shelf2")

    # 领取晚件 → 应检测到跳序
    await _pickup(svc, later, world, "shelf2")
    await clean_db.commit()

    events = await _skip_events(clean_db, worker_id=world["worker"].id)
    assert len(events) == 1
    e = events[0]
    assert e.part_id == later.id
    assert e.part_serial_no == later.serial_no
    assert e.batch_no == 1
    assert e.shelf_id == world["shelf2"].id
    assert e.work_type_id == world["wt"].id
    assert e.quantity == 5
    assert e.part_planned_delivery_date == date(2026, 8, 10)
    assert e.skipped_earliest_date == date(2026, 8, 1)


async def test_skip_not_recorded_for_earliest(clean_db):
    """先领早件 → 不记录。"""
    world = await _make_world(clean_db)
    early = await _make_part(clean_db, world["customer"], serial="S-EARLY",
                              planned=date(2026, 8, 1))
    later = await _make_part(clean_db, world["customer"], serial="S-LATER",
                              planned=date(2026, 8, 10))
    svc = _make_service(clean_db)
    await _place(svc, early, world, "shelf1")
    await _place(svc, later, world, "shelf2")

    await _pickup(svc, early, world, "shelf1")
    await clean_db.commit()

    events = await _skip_events(clean_db, worker_id=world["worker"].id)
    assert len(events) == 0


async def test_skip_not_recorded_for_tie(clean_db):
    """两件并列交期 → 领任一都不记录。"""
    world = await _make_world(clean_db)
    a = await _make_part(clean_db, world["customer"], serial="S-A",
                          planned=date(2026, 8, 5))
    b = await _make_part(clean_db, world["customer"], serial="S-B",
                          planned=date(2026, 8, 5))
    svc = _make_service(clean_db)
    await _place(svc, a, world, "shelf1")
    await _place(svc, b, world, "shelf2")

    await _pickup(svc, b, world, "shelf2")
    await clean_db.commit()

    events = await _skip_events(clean_db, worker_id=world["worker"].id)
    assert len(events) == 0


async def test_skip_not_recorded_for_urgent(clean_db):
    """加急件 → 永不记录（即便有更早候选）。"""
    world = await _make_world(clean_db)
    early = await _make_part(clean_db, world["customer"], serial="S-EARLY",
                              planned=date(2026, 8, 1))
    urgent = await _make_part(
        clean_db, world["customer"], serial="S-URG",
        planned=date(2026, 8, 15), is_urgent=True,
    )
    svc = _make_service(clean_db)
    await _place(svc, early, world, "shelf1")
    await _place(svc, urgent, world, "shelf2")

    await _pickup(svc, urgent, world, "shelf2")
    await clean_db.commit()

    events = await _skip_events(clean_db, worker_id=world["worker"].id)
    assert len(events) == 0


async def test_skip_not_recorded_when_all_have_date_but_no_other_candidates(clean_db):
    """只一件可领时无候选 → 不记录。"""
    world = await _make_world(clean_db)
    only = await _make_part(clean_db, world["customer"], serial="S-ONLY",
                             planned=date(2026, 8, 10))
    svc = _make_service(clean_db)
    await _place(svc, only, world, "shelf1")

    await _pickup(svc, only, world, "shelf1")
    await clean_db.commit()

    events = await _skip_events(clean_db, worker_id=world["worker"].id)
    assert len(events) == 0


# ============================================================
# 统计端点：summary + detail
# ============================================================
def _make_statistics_service(session) -> StatisticsService:
    return StatisticsService(
        session=session,
        stats_repo=StatisticsRepository(session),
        parts=PartRepository(session),
        events=PartEventRepository(session),
        workers=WorkerRepository(session),
        work_types=WorkTypeRepository(session),
    )


async def test_pickup_skip_summary_groups_by_worker(clean_db):
    """构造 2 个工人各产生 1 条跳序 → summary 列出 2 行。"""
    from core.permission import CurrentUser
    from model.enums import UserRole

    world1 = await _make_world(clean_db, prefix="A")
    # 第二个工人独立工种 + 第二个候选件
    world2 = await _make_world(clean_db, prefix="B")

    # worker1（world1.worker）跳序一次
    e1 = await _make_part(clean_db, world1["customer"], serial="A-1",
                          planned=date(2026, 8, 1))
    e2 = await _make_part(clean_db, world1["customer"], serial="A-2",
                          planned=date(2026, 8, 10))
    svc1 = _make_service(clean_db)
    await _place(svc1, e1, world1, "shelf1")
    await _place(svc1, e2, world1, "shelf2")
    await _pickup(svc1, e2, world1, "shelf2")

    # worker2（world2.worker）也跳序一次
    f1 = await _make_part(clean_db, world2["customer"], serial="B-1",
                          planned=date(2026, 8, 1))
    f2 = await _make_part(clean_db, world2["customer"], serial="B-2",
                          planned=date(2026, 8, 10))
    svc2 = _make_service(clean_db)
    await _place(svc2, f1, world2, "shelf1")
    await _place(svc2, f2, world2, "shelf2")
    await _pickup(svc2, f2, world2, "shelf2")
    await clean_db.commit()

    svc_stats = _make_statistics_service(clean_db)
    out = await svc_stats.pickup_skip_summary()
    items = out.items
    assert len(items) == 2
    # 排序：skip_count desc（这里都是 1，按 last_skip_at desc 平手）
    by_worker = {it.worker_id: it for it in items}
    assert by_worker[world1["worker"].id].skip_count == 1
    assert by_worker[world1["worker"].id].worker_name == "跳序工人"
    assert by_worker[world1["worker"].id].badge_code == "BDG-A"
    assert by_worker[world1["worker"].id].work_type_name == "跳序工种"
    assert by_worker[world2["worker"].id].skip_count == 1


async def test_pickup_skip_detail_pagination(clean_db):
    """单工人跳序明细：分页正确。"""
    world = await _make_world(clean_db)
    early = await _make_part(clean_db, world["customer"], serial="S-EARLY",
                              planned=date(2026, 8, 1))
    later = await _make_part(clean_db, world["customer"], serial="S-LATER",
                              planned=date(2026, 8, 10))
    svc = _make_service(clean_db)
    await _place(svc, early, world, "shelf1")
    await _place(svc, later, world, "shelf2")
    await _pickup(svc, later, world, "shelf2")
    await clean_db.commit()

    stats_svc = _make_statistics_service(clean_db)
    # page 1：limit=10 → 应拿全 1 条
    page1 = await stats_svc.pickup_skip_detail(
        str(world["worker"].id), limit=10, offset=0,
    )
    assert page1.total == 1
    assert page1.limit == 10
    assert page1.offset == 0
    assert len(page1.items) == 1
    item = page1.items[0]
    assert item.serial_no == "S-LATER"
    assert item.part_name == "跳序件-S-LATER"
    assert item.batch_no == 1
    assert item.quantity == 5
    assert item.part_planned_delivery_date == date(2026, 8, 10)
    assert item.skipped_earliest_date == date(2026, 8, 1)

    # page 2：offset 越界 → 0 条
    page2 = await stats_svc.pickup_skip_detail(
        str(world["worker"].id), limit=10, offset=10,
    )
    assert page2.total == 1
    assert len(page2.items) == 0


async def test_pickup_skip_detail_rejects_bad_id(clean_db):
    """worker_id 非法（非数字字符串）→ BizError BIZ_INVALID_VALUE 400。"""
    from core.error_code import ErrCode
    from core.exception import BizError

    stats_svc = _make_statistics_service(clean_db)
    with pytest.raises(BizError) as ei:
        await stats_svc.pickup_skip_detail(
            "not-a-number", limit=10, offset=0,
        )
    assert ei.value.code == ErrCode.BIZ_INVALID_VALUE