"""送货单事件辅助（2026-07-22 新增）.

用于 ITEM_ADDED / ITEM_REMOVED / EDITED 等**非状态机迁移**事件——直接构造
TDeliveryNoteEvent 并 flush（受 `event_repo` 加 session）。
状态机迁移事件（SUBMITTED / RECALLED / PICKED_UP / ARCHIVED）由
`DeliveryNoteStateMachine` callbacks 通过 `event_repo.add` 写。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from model.delivery_note_event import TDeliveryNoteEvent
from model.enums import DeliveryNoteEventType


async def write_item_added(
    session: AsyncSession,
    *,
    note_id: int,
    added_serial_nos: list[str],
    note: str | None = None,
    created_by: int | None = None,
) -> None:
    """记录一行 ITEM_ADDED 事件。flush 由 caller 负责（与主流转场一致）。"""
    session.add(TDeliveryNoteEvent(
        delivery_note_id=note_id,
        event_type=DeliveryNoteEventType.ITEM_ADDED.value,
        from_status=None,
        to_status=None,
        note=(
            f"add serials: {', '.join(added_serial_nos[:20])}"
            f"{' 等' if len(added_serial_nos) > 20 else ''}"
        ) if added_serial_nos else note,
        created_by=created_by,
    ))


async def write_item_removed(
    session: AsyncSession,
    *,
    note_id: int,
    removed_serial_nos: list[str],
    note: str | None = None,
    created_by: int | None = None,
) -> None:
    session.add(TDeliveryNoteEvent(
        delivery_note_id=note_id,
        event_type=DeliveryNoteEventType.ITEM_REMOVED.value,
        note=(
            f"remove serials: {', '.join(removed_serial_nos[:20])}"
            f"{' 等' if len(removed_serial_nos) > 20 else ''}"
        ) if removed_serial_nos else note,
        created_by=created_by,
    ))


async def write_pickup_scan(
    session: AsyncSession,
    *,
    note_id: int,
    drawing_code: str,
    badge_code: str | None,
    scanned_count: int,
    expected_count: int,
    note: str | None = None,
    created_by: int | None = None,
) -> None:
    """记录一行 PICKUP_SCANNED 事件（累积扫码进度，不影响状态机）。"""
    session.add(TDeliveryNoteEvent(
        delivery_note_id=note_id,
        event_type=DeliveryNoteEventType.PICKUP_SCANNED.value,
        drawing_code=drawing_code,
        badge_code=badge_code,
        note=note,
        scanned_count=scanned_count,
        expected_count=expected_count,
        created_by=created_by,
    ))


async def write_created(
    session: AsyncSession,
    *,
    note_id: int,
    note: str | None = None,
    created_by: int | None = None,
) -> None:
    session.add(TDeliveryNoteEvent(
        delivery_note_id=note_id,
        event_type=DeliveryNoteEventType.CREATED.value,
        note=note,
        created_by=created_by,
    ))
