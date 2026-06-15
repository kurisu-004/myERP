from collections.abc import AsyncGenerator

from fastapi import Depends

from core.database import SessionLocal
from repository.order import OrderRepository
from repository.part import PartRepository
from repository.unit_of_work import UnitOfWork
from repository.user import UserRepository
from repository.worker import WorkerRepository
from service.order import OrderService
from service.user import UserService


async def get_uow() -> AsyncGenerator[UnitOfWork, None]:
    async with SessionLocal() as session:
        yield UnitOfWork(
            session=session,
            users=UserRepository(session),
            orders=OrderRepository(session),
            parts=PartRepository(session),
            workers=WorkerRepository(session),
        )


def get_user_service(uow: UnitOfWork = Depends(get_uow)) -> UserService:
    return UserService(uow)


def get_order_service(uow: UnitOfWork = Depends(get_uow)) -> OrderService:
    return OrderService(uow)
