from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from repository.order import OrderRepository
from repository.part import PartRepository
from repository.user import UserRepository
from repository.worker import WorkerRepository


@dataclass
class UnitOfWork:
    session: AsyncSession
    users: UserRepository
    orders: OrderRepository
    parts: PartRepository
    workers: WorkerRepository

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
