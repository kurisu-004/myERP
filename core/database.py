import asyncio
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

    # 启动 dashboard 推送后台任务
    from api.v1.ws import dashboard_push_loop

    push_task = asyncio.create_task(dashboard_push_loop())
    print("dashboard push loop started")

    try:
        yield
    finally:
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass
        await engine.dispose()