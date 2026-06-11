from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart
from model.enums import PartStatus


class PartRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, part: TPart) -> TPart:
        self.session.add(part)
        await self.session.flush()
        return part

    async def create_many(self, parts: list[TPart]) -> list[TPart]:
        self.session.add_all(parts)
        await self.session.flush()
        return parts

    async def get_by_id(self, part_id: int) -> TPart | None:
        return await self.session.get(TPart, part_id)

    async def list_by_order(self, order_id: int) -> list[TPart]:
        result = await self.session.execute(
            select(TPart).where(TPart.order_id == order_id).order_by(TPart.id)
        )
        return list(result.scalars().all())

    async def list_by_worker(self, worker_id: int) -> list[TPart]:
        result = await self.session.execute(
            select(TPart).where(TPart.worker_id == worker_id).order_by(TPart.id)
        )
        return list(result.scalars().all())

    async def list_in_process_by_order(self, order_id: int) -> list[TPart]:
        result = await self.session.execute(
            select(TPart)
            .where(TPart.order_id == order_id, TPart.status == PartStatus.IN_PROCESS)
            .order_by(TPart.id)
        )
        return list(result.scalars().all())

    async def list_in_process_by_worker(self, worker_id: int) -> list[TPart]:
        result = await self.session.execute(
            select(TPart)
            .where(TPart.worker_id == worker_id, TPart.status == PartStatus.IN_PROCESS)
            .order_by(TPart.id)
        )
        return list(result.scalars().all())

    async def update(self, part: TPart) -> TPart:
        await self.session.flush()
        return part

    async def delete(self, part: TPart) -> None:
        await self.session.delete(part)
        await self.session.flush()
