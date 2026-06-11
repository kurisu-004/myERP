from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TOrder, TPart, TWorker
from model.enums import PartStatus


class WorkerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, worker: TWorker) -> TWorker:
        self.session.add(worker)
        await self.session.flush()
        return worker

    async def get_by_id(self, worker_id: int) -> TWorker | None:
        return await self.session.get(TWorker, worker_id)

    async def get_by_name(self, name: str) -> TWorker | None:
        result = await self.session.execute(
            select(TWorker).where(TWorker.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[TWorker]:
        result = await self.session.execute(
            select(TWorker).order_by(TWorker.id)
        )
        return list(result.scalars().all())

    async def list_in_process_parts_with_orders(
        self, worker_id: int
    ) -> list[tuple[TPart, TOrder]]:
        stmt = (
            select(TPart, TOrder)
            .join(TOrder, TPart.order_id == TOrder.id)
            .where(
                TPart.worker_id == worker_id,
                TPart.status == PartStatus.IN_PROCESS,
            )
            .order_by(TPart.id)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]
