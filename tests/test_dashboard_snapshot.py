"""Dashboard 大屏 in_process 区段集成测试。

回归：service/dashboard.py::_fetch_in_process_worker + _fetch_worker_names
- 工人在被 deactivate/软删后仍持有批次 → 大屏 in_process 必须保留该批次 + 工人姓名
  （修复前：worker_subq 过滤掉脱岗工人，_fetch_worker_names 过滤掉 deleted_at，
   导致 chip 整段消失 / 姓名变 null）
- 全场 in_process 批次远超 20 条时不得被全局截断
  （修复前：ORDER BY id DESC + LIMIT 20 让老批次被挤出 top-N，
   header 「件」计数与实际 WIP 不一致）
- 工人加工区段不得扩域（不在工人手上的批次不能因放宽谓词而进 in_process）
- per-worker 排序仍按加急优先（确保未来加 per-bucket 限流时不会先丢加急件）
"""
from __future__ import annotations

import pytest

from model import (
    TCustomer,
    TPart,
    TPartBatch,
    TProcess,
    TShelf,
    TShelfProcess,
    TWorker,
    TWorkType,
)
from model.enums import PartEventType, PartLocation, PartStatus, ShelfZone
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
from service.dashboard import build_snapshot
from service.part import PartService
from tests.conftest import seed_root_batch
from tests.test_part_batch import _batches, _make_part, _make_service, _make_world

pytestmark = pytest.mark.asyncio


async def _place(svc, part, world):
    return await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world["shelf"].id,
            next_process_id=world["process"].id,
        ),
    )


def _find_in_process(snapshot: dict, batch_id: int) -> dict | None:
    for item in snapshot["in_process"]:
        if item.get("batch_id") == str(batch_id):
            return item
    return None


# ============================================================
# 根因 1：工人 deactivate/软删后仍持批次 → 大屏必须保留
# ============================================================
async def test_in_process_survives_worker_deactivation(clean_db):
    world = await _make_world(clean_db)
    part = await _make_part(clean_db, world["customer"], qty=50)
    svc = _make_service(clean_db)
    await _place(svc, part, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))

    # 领取后大屏必须看见
    snapshot_before = await build_snapshot(clean_db)
    picked_batch = next(
        b for b in (await _batches(clean_db, part.id))
        if b.location == PartLocation.WORKER.value
    )
    item_before = _find_in_process(snapshot_before, picked_batch.id)
    assert item_before is not None, (
        "领取后 in_process 必须含该批次"
    )
    assert item_before["current_holder_id"] == str(world["worker"].id)
    assert item_before["worker_name"] == world["worker"].name

    # 工人下班：deactivate 同时把 is_active=False + deleted_at=now（service/worker.py:173-174）
    # 直接走 repo 写：service/worker.py::deactivate 的 _worker_to_out 在 async session
    # 中读 updated_at 会触发 MissingGreenlet（flush 后 onupdate=func.now() 标过期），
    # 这是 worker service 的预存问题，不在本次修复范围
    worker_repo = WorkerRepository(clean_db)
    w = await worker_repo.get_by_id(world["worker"].id, include_deleted=True)
    w.is_active = False
    w.deleted_at = __import__("core.time", fromlist=["now_naive"]).now_naive()
    await worker_repo.update(w)
    await clean_db.refresh(w)
    assert w.is_active is False
    assert w.deleted_at is not None

    # 重建快照：批次必须仍在；holder 仍指向该 worker；姓名仍解析
    snapshot_after = await build_snapshot(clean_db)
    item_after = _find_in_process(snapshot_after, picked_batch.id)
    assert item_after is not None, (
        "工人 deactivate 后 in_process 仍必须含该批次（脱岗/下班不应让大屏静默丢批）"
    )
    assert item_after["current_holder_id"] == str(world["worker"].id)
    assert item_after["worker_name"] == world["worker"].name, (
        "脱岗工人姓名仍应解析（修复前 _fetch_worker_names 用 deleted_at 过滤掉）"
    )


# ============================================================
# 根因 2：25 条全厂 in_process 不被全局截断
# ============================================================
async def test_in_process_not_capped_at_top_n(clean_db):
    """两个工人 × (13 + 12) = 25 条批次领取后必须全部出现在 in_process。

    修复前 ORDER BY id DESC + LIMIT 20 会丢 5 条。
    """
    cust = TCustomer(name="截断回归客户", parent_id=None)
    cust.serial_prefix = "T"
    shelf = TShelf(code="TS-1", name="生产架", zone=ShelfZone.PRODUCTION.value)
    process = TProcess(
        code="PROC-T1", name="工序1", category="INHOUSE", sort_order=0,
    )
    wt = TWorkType(code="WT-T", name="工种")
    worker_a = TWorker(badge_code="BDG-TA", name="工人甲", is_active=True)
    worker_b = TWorker(badge_code="BDG-TB", name="工人乙", is_active=True)
    clean_db.add_all([cust, shelf, process, wt, worker_a, worker_b])
    await clean_db.flush()
    clean_db.add(TShelfProcess(shelf_id=shelf.id, process_id=process.id, sort_order=0))
    worker_a.work_type_id = wt.id
    worker_b.work_type_id = wt.id
    await clean_db.flush()
    world = {
        "customer": cust, "shelf": shelf, "insp_shelf": None,
        "process": process, "process2": None, "wt": wt, "worker": worker_a,
    }

    svc = _make_service(clean_db)
    picked_batch_ids: set[int] = set()
    for i in range(13):
        part = await _make_part(clean_db, cust, qty=5, serial=f"T-A{i:03d}")
        await _place(svc, part, world)
        await svc.pick_up_by_scan(PartPickUpRequest(
            serial_no=part.serial_no,
            shelf_id=shelf.id,
            badge_code=worker_a.badge_code,
        ))
        for b in await _batches(clean_db, part.id):
            if b.location == PartLocation.WORKER.value:
                picked_batch_ids.add(b.id)
    for i in range(12):
        part = await _make_part(clean_db, cust, qty=5, serial=f"T-B{i:03d}")
        await _place(svc, part, world)
        await svc.pick_up_by_scan(PartPickUpRequest(
            serial_no=part.serial_no,
            shelf_id=shelf.id,
            badge_code=worker_b.badge_code,
        ))
        for b in await _batches(clean_db, part.id):
            if b.location == PartLocation.WORKER.value:
                picked_batch_ids.add(b.id)

    assert len(picked_batch_ids) == 25

    snapshot = await build_snapshot(clean_db)
    returned_batch_ids = {int(item["batch_id"]) for item in snapshot["in_process"]}
    assert returned_batch_ids == picked_batch_ids, (
        f"in_process 应含全部 25 条；缺 {picked_batch_ids - returned_batch_ids}，"
        f"多 {returned_batch_ids - picked_batch_ids}；"
        "若仅为 20 条且等于 set 末 20 个 id，则是被全局截断"
    )


# ============================================================
# 修复防护：放宽谓词后不得扩域（非工人持有不进 in_process）
# ============================================================
async def test_in_process_excludes_non_worker_holders(clean_db):
    world = await _make_world(clean_db)
    svc = _make_service(clean_db)

    # ① 工单 A：放生产架后不领
    part_a = await _make_part(clean_db, world["customer"], qty=10, serial="E0001")
    await _place(svc, part_a, world)

    # ② 工单 B：放生产架 + 领 + 送检 → status=INSPECTION + location=INSPECTION_SHELF
    part_b = await _make_part(clean_db, world["customer"], qty=10, serial="E0002")
    await _place(svc, part_b, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part_b.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    await svc.scan_event(__import__("schema.part", fromlist=["PartScanRequest"]).PartScanRequest(
        serial_no=part_b.serial_no,
        event_type=PartEventType.INSPECTED,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
        target_inspection_shelf_id=world["insp_shelf"].id,
    ))

    snapshot = await build_snapshot(clean_db)
    in_process_batch_ids = {int(item["batch_id"]) for item in snapshot["in_process"]}

    a_batches = await _batches(clean_db, part_a.id)
    b_batches = await _batches(clean_db, part_b.id)
    a_ids = {b.id for b in a_batches}
    b_ids = {b.id for b in b_batches}

    assert in_process_batch_ids.isdisjoint(a_ids), (
        "在生产架上的批次不得进 in_process（location != WORKER）"
    )
    assert in_process_batch_ids.isdisjoint(b_ids), (
        "已送检的批次（status=INSPECTION）不得进 in_process"
    )


# ============================================================
# per-bucket 排序锁定：同工人多批次时 urgent 必须在前
# ============================================================
async def test_in_process_orders_urgent_first_within_worker(clean_db):
    world = await _make_world(clean_db)
    svc = _make_service(clean_db)

    # 普通件（先领）
    part_normal = await _make_part(clean_db, world["customer"], qty=5, serial="U0001")
    await _place(svc, part_normal, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part_normal.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))

    # 加急件（后领，ORDER BY id ASC 决定它在 SQL 末位；ORDER BY is_urgent DESC 把它拉到前）
    part_urgent = await _make_part(clean_db, world["customer"], qty=5, serial="U0002")
    part_urgent.is_urgent = True
    await clean_db.flush()
    await _place(svc, part_urgent, world)
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part_urgent.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))

    urgent_batch = next(
        b for b in (await _batches(clean_db, part_urgent.id))
        if b.location == PartLocation.WORKER.value
    )
    normal_batch = next(
        b for b in (await _batches(clean_db, part_normal.id))
        if b.location == PartLocation.WORKER.value
    )

    snapshot = await build_snapshot(clean_db)
    # 仅取该工人名下的 in_process 行
    items = [
        i for i in snapshot["in_process"]
        if i["worker_name"] == world["worker"].name
    ]
    assert len(items) == 2
    assert int(items[0]["batch_id"]) == urgent_batch.id, (
        "加急件必须排在普通件之前（ORDER BY is_urgent DESC, planned_delivery_date ASC, id ASC）"
    )
    assert int(items[1]["batch_id"]) == normal_batch.id
