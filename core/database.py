from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from core.config import settings

DATABASE_URL = settings.database_url

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动心跳
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        print("启动心跳")

    # 启动 7 天自动完成后台循环（PR-D 2026-07-10）。
    # 2026-09-15 Phase 6：默认开（向后兼容本地开发）；生产/staging 部署
    # 设 PYTHON_AUTO_COMPLETE_ENABLED=false 让 Rust 后台接管（见
    # backend-rust/src/task/auto_complete.rs），避免双跑推重复 COMPLETED 事件。
    import asyncio
    if settings.auto_complete_enabled:
        from service.auto_complete import auto_complete_loop
        app.state.auto_complete_task = asyncio.create_task(
            auto_complete_loop(), name="auto_complete_loop",
        )
    else:
        # 显式 None 占位：测试可断言 disabled 时 lifespan 不 spawn task
        app.state.auto_complete_task = None

    try:
        yield
    finally:
        # 取消后台循环并等其退出（disabled 时 task 为 None，跳过）
        if app.state.auto_complete_task is not None:
            app.state.auto_complete_task.cancel()
            try:
                await app.state.auto_complete_task
            except asyncio.CancelledError:
                pass
        await engine.dispose()