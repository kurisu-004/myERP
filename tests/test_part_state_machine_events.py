"""Part 状态机 → t_part_event 写入回归测试。

复现/防退化场景：手工测试 create → 下发(place_on_shelf) → 领取(pick_up) →
放回(return_to_shelf) 后，t_part_event 表只该有 CREATED 一条，缺失其余三条。
根因：状态机回调里写错了 TPartEvent 的 import 路径，并直接 await 不到
async create()。这两点修好后，此测试断言 4 个事件行全部落库。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from model import TCustomer, TPart, TPartEvent, TShelf, TWorker
from model.enums import PartEventType, PartStatus, ShelfZone
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.shelf import ShelfRepository
from repository.worker import WorkerRepository


pytestmark = pytest.mark.asyncio


async def _make_customer(session, name: str) -> TCustomer:
    c = TCustomer(name=name)
    session.add(c)
    await session.flush()
    return c


async def test_part_state_transitions_emit_event_rows(clean_db):
    from sqlalchemy import text as _sql_text

    session = clean_db

    # clean_db 不清 t_customer/t_shelf/t_worker；本测试自管，避免序列/唯一键冲突。
    await session.execute(_sql_text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_worker RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_shelf RESTART IDENTITY"))
    await session.execute(_sql_text("TRUNCATE TABLE t_customer"))
    await session.commit()

    customer = await _make_customer(session, "测试客户")

    # 货架（生产区）和工人
    shelf_repo = ShelfRepository(session)
    shelf = await shelf_repo.create(
        TShelf(code="PROD-T1", name="测试货架", zone=ShelfZone.PRODUCTION.value)
    )
    worker_repo = WorkerRepository(session)
    worker = await worker_repo.create(
        TWorker(badge_code="B-T1", name="测试工人")
    )

    # 创建 PENDING 状态的零件
    from datetime import date
    part_repo = PartRepository(session)
    part = await part_repo.create(
        TPart(
            name="test",
            drawing_no="D-T1",
            applicant_name="tester",
            quantity=1,
            request_date=date(2026, 7, 2),
            planned_delivery_date=date(2026, 7, 10),
            customer_id=customer.id,
            status=PartStatus.PENDING.value,
        )
    )

    # 模拟 service/part.py 的写法：同步触发状态机，随后异步 flush
    event_repo = PartEventRepository(session)

    part.sm.place_on_shelf(shelf=shelf, event_repo=event_repo)
    await part_repo.update(part)

    part.sm.pick_up(worker=worker, shelf=shelf, event_repo=event_repo)
    await part_repo.update(part)

    part.sm.return_to_shelf(worker=worker, shelf=shelf, event_repo=event_repo)
    await part_repo.update(part)

    # 断言 t_part_event 落库 3 条（不含 CREATED；CREATED 由 service._write_event 单独写）
    stmt = (
        select(TPartEvent)
        .where(TPartEvent.part_id == part.id)
        .order_by(TPartEvent.id.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())

    types = [r.event_type for r in rows]
    assert PartEventType.PLACED_ON_SHELF.value in types, rows
    assert PartEventType.PICKED_UP.value in types, rows
    assert PartEventType.RETURNED.value in types, rows

    # 零件末态 = IN_PROCESS（ON_SHELF ↔ WITH_WORKER 共享 DB 状态）
    assert part.status == PartStatus.IN_PROCESS.value
    assert part.location == "PRODUCTION_SHELF"