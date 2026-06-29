from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from repository import (
    CustomerRepository,
    PartEventRepository,
    PartRepository,
    WorkerRepository,
)
from service import CustomerService, PartService, WorkerService


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """请求级 Session。正常返回时自动 commit，异常时回滚。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_part_service(
    session: AsyncSession = Depends(get_session),
) -> PartService:
    """注入 PartService，并把 dashboard 广播器作为闭包传入。

    闭包内自带独立 SessionLocal，不复用请求 session（请求 session 此时已经
    commit/rollback，避免在事件触发瞬间读到不一致的数据）。
    """
    from api.v1.ws import broadcast_dashboard_snapshot

    async def _broadcaster() -> None:
        await broadcast_dashboard_snapshot()

    return PartService(
        parts=PartRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        events=PartEventRepository(session),
        broadcaster=_broadcaster,
    )


def get_worker_service(
    session: AsyncSession = Depends(get_session),
) -> WorkerService:
    return WorkerService(workers=WorkerRepository(session))


def get_customer_service(
    session: AsyncSession = Depends(get_session),
) -> CustomerService:
    return CustomerService(customers=CustomerRepository(session))