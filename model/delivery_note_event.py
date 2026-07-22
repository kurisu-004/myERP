"""送货单事件表 (t_delivery_note_event, 2026-07-22 新增).

append-only 审计表，承载送货单全生命周期事件流；与 `t_part_event` / `t_outsource_quote_event`
形态对称。
使用 `EventTimestampMixin`（只要 `created_at`，无 OCC / 无软删）。

注意：不要在本类重复声明 `created_at`，否则会覆盖 mixin 的
`nullable=False, server_default=func.now()`，触发 INSERT NULL 违例
（参见 2026-07-23 production bug fix）。
"""
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import EventTimestampMixin
from model.base import Base
from utils.id_gen import new_id

if TYPE_CHECKING:
    pass


class TDeliveryNoteEvent(Base, EventTimestampMixin):
    """送货单状态变更事件。

    Columns:
        id (BigInteger PK): 雪花 ID
        delivery_note_id (BigInteger NOT NULL): 逻辑外键 → t_delivery_note.id
        event_type (String(32) NOT NULL): CREATED / EDITED / ITEM_ADDED / ITEM_REMOVED /
                                          SUBMITTED / RECALLED / PICKUP_SCANNED /
                                          PICKED_UP / ARCHIVED
        from_status / to_status (String(16) NULL)
        drawing_code (String(100) NULL): 扫码时传入的图纸码（serial_no）
        badge_code (String(50) NULL): 扫码时传入的工牌码
        note (String(500) NULL)
        scanned_count / expected_count (Integer NULL): 累积扫描进度（仅 PICKUP_SCANNED）
        created_by (BigInteger NULL): t_user.id
        (EventTimestampMixin: created_at  NOT NULL DEFAULT now())
    """

    __tablename__ = "t_delivery_note_event"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    delivery_note_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True,
        comment="逻辑外键 → t_delivery_note.id",
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="CREATED / EDITED / ITEM_ADDED / ITEM_REMOVED / SUBMITTED / RECALLED "
                "/ PICKUP_SCANNED / PICKED_UP / ARCHIVED",
    )
    from_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="状态机前态",
    )
    to_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="状态机后态",
    )
    drawing_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="扫码时传入的图纸码（serial_no）",
    )
    badge_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="扫码时传入的工牌码",
    )
    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="事件备注 / 扩展元数据",
    )
    scanned_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="累积扫描次数（仅 PICKUP_SCANNED 事件）",
    )
    expected_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="本单所需扫描总数（仅 PICKUP_SCANNED 事件）",
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="操作用户 t_user.id",
    )
    # 不重复声明 created_at——见模块顶部 docstring。

