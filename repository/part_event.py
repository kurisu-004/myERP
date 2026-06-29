from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPartEvent


class PartEventRepository:
    """t_part_event 数据访问。事件流只追加，不修改不删除。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, event: TPartEvent) -> TPartEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    # ===== 列表 =====
    async def list_by_part(
        self, part_id: int, *, limit: int = 200
    ) -> list[TPartEvent]:
        """按 part_id 查事件流，按 created_at 升序返回（旧→新）。"""
        stmt = (
            select(TPartEvent)
            .where(TPartEvent.part_id == part_id)
            .order_by(TPartEvent.created_at.asc(), TPartEvent.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_for_part(self, part_id: int) -> TPartEvent | None:
        """取该零件最近一条事件。"""
        stmt = (
            select(TPartEvent)
            .where(TPartEvent.part_id == part_id)
            .order_by(TPartEvent.created_at.desc(), TPartEvent.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()