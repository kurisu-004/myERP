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

    # 2026-09-17 STS 端口 PR：auto_complete 由 backend-rust v2 task/auto_complete.rs
    # 接管，本仓不再启动此 loop（service/auto_complete.py 已删除）。保留
    # settings.auto_complete_enabled 字段以兼容 .env，但 lifespan 不再消费。
    app.state.auto_complete_task = None

    try:
        yield
    finally:
        # 无后台任务需要取消
        await engine.dispose()