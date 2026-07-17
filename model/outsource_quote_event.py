"""外协报价事件表（t_outsource_quote_event，2026-07-16 新增）。

append-only 审计表，承载报价单状态转换的事件流；与 `t_part_event` 形态对称。
使用 `EventTimestampMixin`（只要 `created_at`，不要 version / updated_at / 软删）。
"""
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import EventTimestampMixin
from model.base import Base
from utils.id_gen import new_id

if TYPE_CHECKING:
    pass


class TOutsourceQuoteEvent(Base, EventTimestampMixin):
    """外协报价单状态变更事件。

    Columns:
        id (BigInteger PK): 雪花 ID
        quote_id (BigInteger): 逻辑外键 → t_outsource_quote.id
        event_type (String(32)): CREATED / EDITED / SUBMITTED / APPROVED / REJECTED / USED
        from_status (String(16) NULL): 状态机前态
        to_status (String(16) NULL): 状态机后态
        note (String(500) NULL): 事件备注
        created_by (BigInteger NULL): 操作用户 id（逻辑 FK → t_user.id）
        (EventTimestampMixin: created_at)
    """

    __tablename__ = "t_outsource_quote_event"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    quote_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="报价单 id",
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="CREATED / EDITED / SUBMITTED / APPROVED / REJECTED / USED",
    )
    from_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="状态机前态",
    )
    to_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="状态机后态",
    )
    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="事件备注",
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="操作人 user id",
    )
