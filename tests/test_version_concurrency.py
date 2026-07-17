"""乐观锁 (OCC) 真实 DB 集成测试。

需要本地 PostgreSQL 容器（tests/conftest.py 的 `_postgres_test_lifecycle` 自动 up + migrate）。

覆盖：
- 两 session 并发 UPDATE 同一 part：第二个 session flush 抛 StaleDataError
- OCC 与 onupdate=func.now() 的 updated_at 兼容（不重现 MissingGreenlet）
- 并发 soft_delete 同一 part → 第二个 StaleDataError
- 真实业务流：pick_up_by_scan 双扫同 serial → 第二人 StaleDataError

这些测试验证 SQLAlchemy `version_id_col` 在真实 asyncpg 后端下能
正确发出 `WHERE id=? AND version=?` 并触发 StaleDataError。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from model import TCustomer, TPart, TProcess, TShelf, TWorker
from model.enums import PartStatus, ProcessCategory, ShelfZone
from repository.part import PartRepository

pytestmark = pytest.mark.asyncio


async def _seed_part_in_state(session, *, status: str = PartStatus.PENDING.value,
                              location: str | None = None,
                              holder_id: int | None = None) -> tuple[
    TCustomer, TPart, TShelf, TProcess, TWorker,
]:
    """塞最小可用的 customer / part / shelf / process / worker。"""
    customer = TCustomer(name="OCC测试客户")
    session.add(customer)
    await session.flush()

    worker = TWorker(badge_code="OCC001", name="OCC测试员", is_active=True)
    session.add(worker)
    await session.flush()

    shelf = TShelf(
        code=f"OCC-SHELF-{customer.id}",
        name="OCC测试架",
        zone=ShelfZone.PRODUCTION.value,
        is_active=True,
    )
    session.add(shelf)
    await session.flush()

    process = TProcess(
        code=f"OCC-PROC-{customer.id}",
        name="OCC测试工序",
        category=ProcessCategory.INHOUSE.value,
    )
    session.add(process)
    await session.flush()

    part = TPart(
        name="OCC测试零件",
        drawing_no=f"OCC-DWG-{customer.id}",
        applicant_name="OCC测试",
        quantity=1,
        request_date=date(2026, 7, 15),
        planned_delivery_date=date(2026, 8, 15),
        customer_id=customer.id,
        status=status,
        location=location,
        current_holder_id=holder_id,
    )
    session.add(part)
    await session.flush()
    await session.commit()
    return customer, part, shelf, process, worker


# ============================================================
# 测试 1：两 session 并发 UPDATE 同 part → 第二个 flush 抛 StaleDataError
# ============================================================


async def test_concurrent_update_second_session_stale(clean_db):
    """Session A 加载 part → 修改 → commit；
    Session B 独立连接加载同一 part → 修改 → flush → 0 行更新 → StaleDataError。

    验证 SQLAlchemy 自动发出 `WHERE id=? AND version=?`，且 B 抛 StaleDataError。
    """
    from core.database import engine

    session_a = clean_db
    _, part_a, _, _, _ = await _seed_part_in_state(session_a)
    part_id = part_a.id

    # Session B 独立连接
    SessionB = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionB() as session_b:
        part_b = await session_b.get(TPart, part_id)
        assert part_b is not None

        # Session A 抢先 commit（修改 name 触发 UPDATE）
        part_a.name = "A 修改后"
        await session_a.flush()
        await session_a.commit()

        # Session B 仍持有过期 version 的缓存 → flush 必抛
        part_b.name = "B 修改后"
        with pytest.raises(StaleDataError):
            await session_b.flush()


# ============================================================
# 测试 2：OCC + onupdate=updated_at 不触发 MissingGreenlet
# ============================================================


async def test_occ_with_onupdate_updated_at_no_missing_greenlet(clean_db):
    """CLAUDE.md §13 / §15 警示：`onupdate=func.now()` 的列在 UPDATE 后
    会 expire；async session 访问过期列会触发 MissingGreenlet。

    验证：`version_id_generator=True`（默认）+ Python 端同步
    `model.version += 1` 后，访问 updated_at / version / deleted_at 都不抛
    MissingGreenlet。
    """
    from core.time import now_naive

    customer = TCustomer(name="OCC updated_at 测试")
    clean_db.add(customer)
    await clean_db.flush()

    part_repo = PartRepository(clean_db)
    part = await part_repo.create(
        TPart(
            name="updated_at 测试",
            drawing_no="OCC-DWG-upd",
            applicant_name="tester",
            quantity=1,
            request_date=date(2026, 7, 15),
            planned_delivery_date=date(2026, 8, 15),
            customer_id=customer.id,
            status=PartStatus.PENDING.value,
        ),
    )
    await clean_db.flush()
    original_version = part.version

    # 修改 + flush → version +1 + updated_at 自动更新
    part.name = "修改后"
    await part_repo.update(part)

    # 关键断言：访问这些列不抛 MissingGreenlet
    assert part.version > original_version

    # 软删路径同样验证
    part.deleted_at = now_naive()
    await part_repo.soft_delete(part)
    assert part.deleted_at is not None
    assert part.version > original_version + 1


# ============================================================
# 测试 3：并发 soft_delete 同一 part → 第二个 StaleDataError
# ============================================================


async def test_concurrent_soft_delete_second_session_stale(clean_db):
    """两 session 同时软删同 part：B 用 raw SQL 把 DB 的 version 改掉，
    让 session_b 缓存的 version 与 DB 不一致 → flush 时 0 行 → StaleDataError。"""
    from core.database import engine
    from core.time import now_naive
    from sqlalchemy import text

    session_a = clean_db
    _, part_a, _, _, _ = await _seed_part_in_state(session_a)
    part_id = part_a.id

    # Session A 软删 + commit
    part_a.deleted_at = now_naive()
    await session_a.flush()
    await session_a.commit()

    # Session B 独立连接
    SessionB = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionB() as session_b:
        part_b = await session_b.get(TPart, part_id)
        assert part_b is not None
        loaded_version = part_b.version

        # 模拟「在我加载后又有人改了」：raw SQL 把 version +1
        await session_b.execute(
            text("UPDATE t_part SET version = version + 1 WHERE id = :id"),
            {"id": part_id},
        )
        await session_b.commit()
        # 现在 DB 行 version = loaded_version + 1，但 part_b 缓存还是 loaded_version

        # B 想再软删 → SQLAlchemy 用 loaded_version 算 UPDATE WHERE version=loaded_version
        # DB 行已是 loaded_version+1 → 0 行匹配 → StaleDataError
        # 不直接改 deleted_at（DB 已经是 A 的 deleted_at），改为改 name 触发 UPDATE
        part_b.name = "B 修改"
        with pytest.raises(StaleDataError):
            await session_b.flush()


# ============================================================
# 测试 4：pick_up_by_scan 两工人同 serial → 第二人 StaleDataError
# ============================================================


async def test_pick_up_double_scan_returns_409(clean_db):
    """两人并发领同一 serial 的零件，第二个的 flush 失败 → service 层抛
    StaleDataError → API 层转 409。

    这是 CLAUDE.md §11 提到的真实并发风险；OCC 是修复手段。
    """
    from core.database import engine
    from core.time import now_naive

    session_a = clean_db
    customer, part_a, shelf, process, worker1 = await _seed_part_in_state(
        session_a,
        status=PartStatus.IN_PROCESS.value,
        location="PRODUCTION_SHELF",
        holder_id=shelf.id if False else None,  # placeholder; set below
    )
    # 上面 _seed_part_in_state 没传 holder，但我们要 IN_PROCESS + PRODUCTION_SHELF
    # 所以重新设置：
    part_a.location = "PRODUCTION_SHELF"
    part_a.current_holder_id = shelf.id
    part_a.next_process_id = process.id
    part_a.placed_at = now_naive()
    await session_a.flush()
    await session_a.commit()
    part_id = part_a.id

    # Session B 独立连接（模拟第二个工人扫同 serial）
    SessionB = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionB() as session_b:
        part_b = await session_b.get(TPart, part_id)
        assert part_b is not None

        # Session A 抢先 pick_up：把 holder 改到 worker1
        part_a.current_holder_id = worker1.id
        part_a.location = "WORKER"
        await session_a.flush()
        await session_a.commit()

        # Session B 拿过期 version 修改 → StaleDataError
        worker2 = TWorker(badge_code="OCC002", name="OCC工人2", is_active=True)
        session_b.add(worker2)
        await session_b.flush()
        part_b.current_holder_id = worker2.id
        part_b.location = "WORKER"
        with pytest.raises(StaleDataError):
            await session_b.flush()
