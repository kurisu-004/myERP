"""外协报价事件 (OutsourceQuoteEvent) 数据访问。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TOutsourceQuoteEvent


class OutsourceQuoteEventRepository:
    """t_outsource_quote_event 数据访问（append-only 审计）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    def add(self, event: TOutsourceQuoteEvent) -> TOutsourceQuoteEvent:
        """同步 add，供状态机回调使用（无 flush，state machine sm.send 时机一致）。"""
        self.session.add(event)
        return event

    async def create(
        self, event: TOutsourceQuoteEvent,
    ) -> TOutsourceQuoteEvent:
        """异步 flush 版，service 在 commit 前可显式调。"""
        self.session.add(event)
        await self.session.flush()
        return event

    # ===== 列表 =====
    async def list_by_quote(
        self, quote_id: int,
    ) -> list[TOutsourceQuoteEvent]:
        stmt = (
            select(TOutsourceQuoteEvent)
            .where(TOutsourceQuoteEvent.quote_id == quote_id)
            .order_by(TOutsourceQuoteEvent.created_at.asc(), TOutsourceQuoteEvent.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
