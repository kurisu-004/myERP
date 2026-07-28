from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPartEvent
from model.enums import PartEventType


class PartEventRepository:
    """t_part_event 数据访问。事件流只追加，不修改不删除。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    def add(self, event: TPartEvent) -> TPartEvent:
        """同步登记事件，供状态机同步回调调用。

        只执行 ``session.add``，把对象 stage 到当前 session；真正的 INSERT
        留到后续 ``session.flush()``（通常由同一事务内的 ``update`` 触发）。
        异步场景请用 ``create``。
        """
        self.session.add(event)
        return event

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

    # ============================================================
    # 外协对账（2026-07-28 新增）
    # ============================================================
    async def list_sent_to_company(
        self,
        *,
        company_id: int,
        sent_from: datetime | None = None,
        sent_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TPartEvent]:
        """列出发送给某外协公司的所有 SENT_TO_OUTSOURCE 事件，按时间倒序。

        用于外协对账端点：让文员拿这个对账表与外协公司发来的对账单核对。
        """
        stmt = select(TPartEvent).where(
            TPartEvent.event_type == PartEventType.SENT_TO_OUTSOURCE.value,
            TPartEvent.outsource_company_id == company_id,
        )
        if sent_from is not None:
            stmt = stmt.where(TPartEvent.created_at >= sent_from)
        if sent_to is not None:
            stmt = stmt.where(TPartEvent.created_at <= sent_to)
        stmt = stmt.order_by(
            TPartEvent.created_at.desc(), TPartEvent.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_sent_to_company(
        self,
        *,
        company_id: int,
        sent_from: datetime | None = None,
        sent_to: datetime | None = None,
    ) -> int:
        """发送给某外协公司的事件总数（同 list_sent_to_company 谓词）。"""
        stmt = select(func.count(TPartEvent.id)).where(
            TPartEvent.event_type == PartEventType.SENT_TO_OUTSOURCE.value,
            TPartEvent.outsource_company_id == company_id,
        )
        if sent_from is not None:
            stmt = stmt.where(TPartEvent.created_at >= sent_from)
        if sent_to is not None:
            stmt = stmt.where(TPartEvent.created_at <= sent_to)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_received_from_company(
        self,
        *,
        part_ids: list[int],
        company_id: int,
    ) -> list[TPartEvent]:
        """列出这些 part 被该外协公司 RECEIVE 的事件（用于对账端点拼 received_at）。

        服务层按 part_id 分组取最新一条。
        """
        if not part_ids:
            return []
        stmt = (
            select(TPartEvent)
            .where(
                TPartEvent.event_type == PartEventType.RECEIVED_FROM_OUTSOURCE.value,
                TPartEvent.outsource_company_id == company_id,
                TPartEvent.part_id.in_(part_ids),
            )
            .order_by(
                TPartEvent.part_id.asc(),
                TPartEvent.created_at.desc(),
                TPartEvent.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_sent_to_company_for_part(
        self,
        *,
        part_id: int,
        company_id: int,
    ) -> TPartEvent | None:
        """取某 part 最近一次被发送给某外协公司的事件（用于 receive 时推断 company_id）。"""
        stmt = (
            select(TPartEvent)
            .where(
                TPartEvent.event_type == PartEventType.SENT_TO_OUTSOURCE.value,
                TPartEvent.outsource_company_id == company_id,
                TPartEvent.part_id == part_id,
            )
            .order_by(TPartEvent.created_at.desc(), TPartEvent.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()