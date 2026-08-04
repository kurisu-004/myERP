"""工种可领取上限（2026-08-05 任务 5）集成测试。

`TWorkType.max_held_batches` NULL=不限；持有批次数 >= 上限 → 422。
- test_pickup_blocked_at_limit：上限 1，持有 1 → 拒领
- test_pickup_allowed_below_limit：上限 2，持有 1 → 允许
- test_pickup_unlimited_when_null：NULL → 多件都成功
- test_pickup_bypass_when_worker_has_no_work_type：工人无工种 → 跳过检查
- test_work_type_crud_with_limit：CRUD 往返带 max_held_batches
"""
from __future__ import annotations

from datetime import date

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import (
    TCustomer,
    TPart,
    TProcess,
    TShelf,
    TShelfProcess,
    TWorker,
    TWorkType,
)
from model.enums import ShelfZone
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
from schema.part import PartPickUpRequest, PlaceOnShelfRequest
from schema.work_type import WorkTypeCreateRequest, WorkTypeUpdateRequest
from service.part import PartService
from service.work_type import WorkTypeService
from tests.conftest import seed_root_batch

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers
# ============================================================
async def _make_world(
    session, *, prefix: str = "L", max_held_batches: int | None = None,
) -> dict:
    """建客户 + 货架 + 工序 + 映射 + 工种（带上限）+ 工人。

    `prefix` 是 **客户 serial_prefix 字符**（VARCHAR(1)），其他 code 用其派生。
    """
    cust = TCustomer(name=f"上限测试客户-{prefix}", parent_id=None)
    cust.serial_prefix = prefix
    shelf = TShelf(code=f"WL-{prefix}1", name="生产架", zone=ShelfZone.PRODUCTION.value)
    process = TProcess(code=f"WPROC-{prefix}1", name="工序1", category="INHOUSE", sort_order=0)
    wt = TWorkType(
        code=f"WWT-{prefix}", name="工种", sort_order=0,
        max_held_batches=max_held_batches,
    )
    worker = TWorker(
        badge_code=f"WBDG-{prefix}", name="上限工人", is_active=True,
    )
    session.add_all([cust, shelf, process, wt, worker])
    await session.flush()
    session.add(TShelfProcess(shelf_id=shelf.id, process_id=process.id, sort_order=0))
    worker.work_type_id = wt.id
    await session.flush()
    return {
        "customer": cust, "shelf": shelf, "process": process,
        "wt": wt, "worker": worker,
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


def _make_wt_service(session) -> WorkTypeService:
    return WorkTypeService(work_types=WorkTypeRepository(session), current_user=None)


async def _make_part(
    session, customer: TCustomer, *, qty: int = 10, serial: str = "L0001",
) -> TPart:
    part = TPart(
        serial_no=serial,
        name=f"上限件-{serial}",
        drawing_no=f"DWG-{serial}",
        applicant_name="上限测试",
        quantity=qty,
        request_date=date(2026, 8, 1),
        planned_delivery_date=date(2026, 9, 1),
        customer_id=customer.id,
        status="PENDING",
        location="OFFICE",
    )
    session.add(part)
    await session.flush()
    await seed_root_batch(session, part)
    return part


async def _place(session, svc, part, world):
    return await svc.place_on_shelf(
        part.id,
        PlaceOnShelfRequest(
            shelf_id=world["shelf"].id,
            next_process_id=world["process"].id,
        ),
    )


# ============================================================
# pick_up_by_scan 上限拦截
# ============================================================
async def test_pickup_blocked_at_limit(clean_db):
    """上限 1，工人已持有 1 → 第二个工单 place 后再领 → 422 BIZ_WORKER_HOLD_LIMIT_EXCEEDED。"""
    world = await _make_world(clean_db, prefix="K", max_held_batches=1)
    svc = _make_service(clean_db)

    part1 = await _make_part(clean_db, world["customer"], qty=5, serial="L0001")
    part2 = await _make_part(clean_db, world["customer"], qty=5, serial="L0002")
    await _place(clean_db, svc, part1, world)
    await _place(clean_db, svc, part2, world)

    # 领第一个：成功
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part1.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    assert part1.status == "IN_PROCESS"

    # 领第二个：达到上限 → 422
    with pytest.raises(BizError) as exc:
        await svc.pick_up_by_scan(PartPickUpRequest(
            serial_no=part2.serial_no,
            shelf_id=world["shelf"].id,
            badge_code=world["worker"].badge_code,
        ))
    assert exc.value.code == ErrCode.BIZ_WORKER_HOLD_LIMIT_EXCEEDED
    assert exc.value.http_status == 422
    assert "1" in exc.value.message  # 上限数字出现在文案中
    # 第二个工单/批次状态不变
    assert part2.status == "IN_PROCESS"


async def test_pickup_allowed_below_limit(clean_db):
    """上限 2，持有 1 → 第二个仍可领。"""
    world = await _make_world(clean_db, prefix="W", max_held_batches=2)
    svc = _make_service(clean_db)

    part1 = await _make_part(clean_db, world["customer"], qty=5, serial="L0011")
    part2 = await _make_part(clean_db, world["customer"], qty=5, serial="L0012")
    await _place(clean_db, svc, part1, world)
    await _place(clean_db, svc, part2, world)

    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part1.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    # 持有 1 < 2 → 第二个 OK
    await svc.pick_up_by_scan(PartPickUpRequest(
        serial_no=part2.serial_no,
        shelf_id=world["shelf"].id,
        badge_code=world["worker"].badge_code,
    ))
    assert part1.status == "IN_PROCESS"
    assert part2.status == "IN_PROCESS"


async def test_pickup_unlimited_when_null(clean_db):
    """max_held_batches=NULL → 不限。3 个工单都可领。"""
    world = await _make_world(clean_db, prefix="U", max_held_batches=None)
    svc = _make_service(clean_db)

    parts = []
    for i in range(3):
        p = await _make_part(clean_db, world["customer"], qty=5, serial=f"L{i:04d}2")
        await _place(clean_db, svc, p, world)
        parts.append(p)

    for p in parts:
        await svc.pick_up_by_scan(PartPickUpRequest(
            serial_no=p.serial_no,
            shelf_id=world["shelf"].id,
            badge_code=world["worker"].badge_code,
        ))
    for p in parts:
        assert p.status == "IN_PROCESS"


async def test_pickup_bypass_when_worker_has_no_work_type(clean_db):
    """工人未挂工种 → 跳过上限检查，多件都成功。"""
    world = await _make_world(clean_db, prefix="B", max_held_batches=1)
    # 取消工人的 work_type 关联
    world["worker"].work_type_id = None
    await clean_db.flush()

    svc = _make_service(clean_db)
    parts = []
    for i in range(2):
        p = await _make_part(clean_db, world["customer"], qty=5, serial=f"L{i:04d}3")
        await _place(clean_db, svc, p, world)
        parts.append(p)

    for p in parts:
        await svc.pick_up_by_scan(PartPickUpRequest(
            serial_no=p.serial_no,
            shelf_id=world["shelf"].id,
            badge_code=world["worker"].badge_code,
        ))
    for p in parts:
        assert p.status == "IN_PROCESS"


# ============================================================
# WorkTypeService CRUD 透传 max_held_batches
# ============================================================
async def test_work_type_crud_with_limit(clean_db):
    """工种 CRUD 往返带 max_held_batches（含 set 显式 null 清空）。"""
    session = clean_db
    svc = _make_wt_service(session)

    # create 带上限
    out = await svc.create_work_type(WorkTypeCreateRequest(
        code="WT-CRUD-1",
        name="工种1",
        sort_order=0,
        max_held_batches=3,
    ))
    assert out.max_held_batches == 3

    # get_work_type 读回
    fetched = await svc.get_work_type(out.id)
    assert fetched.max_held_batches == 3

    # update 改上限
    out2 = await svc.update_work_type(
        out.id, WorkTypeUpdateRequest(max_held_batches=5),
    )
    assert out2.max_held_batches == 5

    # update 不传 → 不变
    out3 = await svc.update_work_type(
        out.id, WorkTypeUpdateRequest(name="改名"),
    )
    assert out3.max_held_batches == 5
    assert out3.name == "改名"

    # update 显式 None → 清空
    out4 = await svc.update_work_type(
        out.id, WorkTypeUpdateRequest(max_held_batches=None),
    )
    # model_fields_set 包含 None 时会清空；schema 校验 ge=1 阻止 0/负数
    assert out4.max_held_batches is None


async def test_work_type_create_rejects_zero_limit(clean_db):
    """Pydantic ge=1 拦截 0/负数（避免下游 < 0 比较）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkTypeCreateRequest(code="X1", name="x", max_held_batches=0)
    with pytest.raises(ValidationError):
        WorkTypeUpdateRequest(max_held_batches=-1)
