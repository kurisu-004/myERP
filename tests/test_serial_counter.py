"""SerialCounterRepository.acquire_serial 的集成测试。

依赖：
- 本地 PostgreSQL 已起（`docker compose up -d`）。
- 已经 `uv run alembic upgrade head`，t_serial_counter 存在并有 L/F/H 三行。
- 走 `clean_db` fixture 会 truncate t_part / t_part_event / t_serial_counter
  并重新种子 L/F/H——**会破坏本地数据**，不要对生产 DB 跑。

每个测试独立 DB session；用完自动 rollback / commit。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from core.serial import SERIAL_MIN, SERIAL_POOL_SIZE
from model import TPart
from model.enums import PartStatus
from repository.serial_counter import SerialCounterRepository
from utils.id_gen import new_id


pytestmark = pytest.mark.integration


# ============================================================
# 1. 首次分配 = prefix + 1000
# ============================================================
async def test_first_allocation_returns_prefix_plus_min(
    clean_db: AsyncSession,
) -> None:
    repo = SerialCounterRepository(clean_db)
    assert await repo.acquire_serial("F") == f"F{SERIAL_MIN}"  # "F1000"


# ============================================================
# 2. 连续三次分配 = F1000 / F1001 / F1002，counter 同步递增
# ============================================================
async def test_three_in_a_row_increments(clean_db: AsyncSession) -> None:
    repo = SerialCounterRepository(clean_db)
    sns = [await repo.acquire_serial("F") for _ in range(3)]
    assert sns == ["F1000", "F1001", "F1002"]

    counter = (
        await clean_db.execute(
            text("SELECT counter FROM t_serial_counter WHERE prefix = 'F'")
        )
    ).scalar_one()
    assert counter == 3


# ============================================================
# 3. 同 prefix 的并发分配全部得到不同的序列号
# ============================================================
async def test_concurrent_allocations_unique(clean_db: AsyncSession) -> None:
    repo = SerialCounterRepository(clean_db)
    sns = await asyncio.gather(
        *[repo.acquire_serial("L") for _ in range(10)]
    )
    assert len(set(sns)) == 10
    # 应当严格按 counter 升序
    nums = sorted(int(s[1:]) for s in sns)
    assert nums == list(range(SERIAL_MIN, SERIAL_MIN + 10))


# ============================================================
# 4. 占用过的号被释放后，下一次分配复用
# ============================================================
async def test_completed_releases_slot(clean_db: AsyncSession) -> None:
    repo = SerialCounterRepository(clean_db)

    # 第一次拿到 F1000
    first = await repo.acquire_serial("F")
    assert first == "F1000"

    # 模拟 service 层：插入一条 t_part 占住 F1000，然后置 COMPLETED → serial=NULL
    part = TPart(
        id=new_id(),
        serial_no="F1000",
        name="test",
        drawing_no="D-1",
        applicant_name="tester",
        quantity=1,
        unit_price=0,
        total_price=0,
        status=PartStatus.COMPLETED.value,  # 直接置终态，serial_no 不会被分配路径使用
        is_urgent=False,
        customer_id=0,  # 占位，不影响 serial 查询
    )
    clean_db.add(part)
    await clean_db.flush()

    # 由于 status=COMPLETED，F1000 不算"活跃占用"——下一个分配应当复用 F1000
    second = await repo.acquire_serial("F")
    assert second == "F1000"

    counter = (
        await clean_db.execute(
            text("SELECT counter FROM t_serial_counter WHERE prefix = 'F'")
        )
    ).scalar_one()
    assert counter == 2  # 两次成功分配


# ============================================================
# 5. pool 满（5000 活跃占用）→ BIZ_PART_SERIAL_EXHAUSTED
# ============================================================
async def test_pool_exhausted_raises(clean_db: AsyncSession) -> None:
    # 直接 SQL 塞 SERIAL_POOL_SIZE 条活跃 t_part 覆盖 F1000..F5999
    # 走 SQL 绕过 repo 是因为 acquire_serial 一次只分配一个，
    # 走 repo 跑 5000 次会非常慢。
    # t_part.status 在 PENDING/IN_PROCESS/... 时算活跃（见 SERIAL_RELEASE_STATUSES）
    rows = [
        {
            "id": new_id(),
            "serial_no": f"F{SERIAL_MIN + i}",
            "name": f"part-{i}",
            "drawing_no": f"D-{i}",
            "applicant_name": "tester",
            "quantity": 1,
            "unit_price": 0,
            "total_price": 0,
            "status": PartStatus.PENDING.value,
            "is_urgent": False,
            "customer_id": 0,
        }
        for i in range(SERIAL_POOL_SIZE)
    ]
    await clean_db.execute(text("DELETE FROM t_part"))
    # 用 executemany 风格：SQLAlchemy 2.0 async 用 session.execute + 参数列表
    # 走 ORM bulk_save_objects 更简洁
    clean_db.add_all([TPart(**r) for r in rows])
    await clean_db.flush()

    repo = SerialCounterRepository(clean_db)
    with pytest.raises(BizError) as exc_info:
        await repo.acquire_serial("F")
    assert exc_info.value.code == ErrCode.BIZ_PART_SERIAL_EXHAUSTED
    assert exc_info.value.http_status == 409


# ============================================================
# 6. 未注册的 prefix → BIZ_SERIAL_PREFIX_UNKNOWN
# ============================================================
async def test_unknown_prefix_raises(clean_db: AsyncSession) -> None:
    repo = SerialCounterRepository(clean_db)
    with pytest.raises(BizError) as exc_info:
        await repo.acquire_serial("Z")
    assert exc_info.value.code == ErrCode.BIZ_SERIAL_PREFIX_UNKNOWN
    assert exc_info.value.http_status == 400


# ============================================================
# 7. wrap 行为：counter 越过 pool size 后回到 1000
# ============================================================
async def test_wrap_after_pool_size(clean_db: AsyncSession) -> None:
    """counter 超过 pool size 时，candidate_int = SERIAL_MIN + counter % pool
    应当从 1000 重新开始；只要该号在 t_part 里没活跃占用就分配出去。
    """
    # 把 F counter 推到 4999，下一次分配 = F5999
    await clean_db.execute(
        text("UPDATE t_serial_counter SET counter = 4999 WHERE prefix = 'F'")
    )
    await clean_db.commit()

    repo = SerialCounterRepository(clean_db)
    assert await repo.acquire_serial("F") == "F5999"

    # counter 变 5000，公式 SERIAL_MIN + 5000 % 5000 = 1000
    assert await repo.acquire_serial("F") == "F1000"

    counter = (
        await clean_db.execute(
            text("SELECT counter FROM t_serial_counter WHERE prefix = 'F'")
        )
    ).scalar_one()
    assert counter == 5001
