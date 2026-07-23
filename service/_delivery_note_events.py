"""送货单事件辅助（2026-07-23 精简为单一 helper）.

2026-07-22 曾包含 write_item_added / write_item_removed / write_pickup_scan /
write_created 四个 helper；2026-07-23 删掉前三个——非状态机迁移噪音事件按
用户要求不再记录，司机扫码去重/进度由前端本地实现（DispatchNoteList.vue）。
状态机迁移事件（SUBMITTED / WITHDRAWN / PICKED_UP）由
`DeliveryNoteStateMachine` callbacks 通过 `event_repo.add` 写。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from model.delivery_note_event import TDeliveryNoteEvent
from model.enums import DeliveryNoteEventType


async def write_created(
    session: AsyncSession,
    *,
    note_id: int,
    note: str | None = None,
    created_by: int | None = None,
) -> None:
    """记录一行 CREATED 事件（新建草稿）。flush 由 caller 负责。"""
    session.add(TDeliveryNoteEvent(
        delivery_note_id=note_id,
        event_type=DeliveryNoteEventType.CREATED.value,
        note=note,
        created_by=created_by,
    ))