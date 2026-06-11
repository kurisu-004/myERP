from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TOrder, TPart, TWorker
from model.enums import PartStatus


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, order: TOrder) -> TOrder:
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_by_id(self, order_id: int) -> TOrder | None:
        return await self.session.get(TOrder, order_id)

    async def list_by_user(self, user_id: int) -> list[TOrder]:
        result = await self.session.execute(
            select(TOrder).where(TOrder.user_id == user_id).order_by(TOrder.id)
        )
        return list(result.scalars().all())

    async def list_in_process_parts_with_workers(
        self, order_id: int
    ) -> list[tuple[TPart, TWorker]]:
        stmt = (
            select(TPart, TWorker)
            .join(TWorker, TPart.worker_id == TWorker.id)
            .where(
                TPart.order_id == order_id,
                TPart.status == PartStatus.IN_PROCESS,
            )
            .order_by(TPart.id)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]
