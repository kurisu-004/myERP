"""跳序取件事件仓储（2026-08-05）。

事件型 append-only：无 update / soft_delete / get_by_id；只追加 + 统计聚合
查询。聚合方法留在 ``repository/statistics.py`` 里（与既有统计端点同 SQL
风格），本仓仅负责 service 层写入。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from model import TPickupSkipEvent


class PickupSkipEventRepository:
    """t_pickup_skip_event 数据访问。事件流只追加。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, event: TPickupSkipEvent) -> TPickupSkipEvent:
        """插入跳序事件；commit 由调用方所在 session 统一提交。"""
        self.session.add(event)
        await self.session.flush()
        return event