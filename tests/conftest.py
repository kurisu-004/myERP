"""测试 fixtures。

当前项目里 `tests/` 是空的，这是第一组测试。建立两个 fixture：

- `db_session`：从 settings 拿 engine，开一个请求级 session，
  走与生产相同的 commit/rollback 语义。
- `clean_db`：在每个测试开始前清空 `t_part` / `t_part_event` / `t_serial_counter`，
  保证测试间隔离。**会破坏本地 DB 数据**，仅在跑这套测试时使用。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """请求级 session，commit/rollback 行为与生产一致。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def clean_db(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """在测试前清空三张业务表；用完回滚到干净状态。

    ⚠️  会 truncate `t_part` / `t_part_event` / `t_serial_counter`，
    跑测试前确保本地 DB 是「可重置」状态，不要对生产数据使用。
    """
    # t_part_event 是 t_part 的子记录（无物理 FK），先清
    await db_session.execute(text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await db_session.execute(text("TRUNCATE TABLE t_part RESTART IDENTITY"))
    await db_session.execute(
        text("TRUNCATE TABLE t_serial_counter RESTART IDENTITY")
    )
    # 重新种子 L/F/H 三行
    await db_session.execute(
        text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "VALUES ('L', 0), ('F', 0), ('H', 0)"
        )
    )
    await db_session.commit()
    yield db_session
