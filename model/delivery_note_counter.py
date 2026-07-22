"""送货单号每日计数器 (t_delivery_note_counter, 2026-07-22 新增).

单号 `DN-YYYYMMDD-NNNN` 由本表 `last_value` 原子递增发放。
PK = date_ymd（自然日字符串），每天一行。
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base

if TYPE_CHECKING:
    pass


class TDeliveryNoteCounter(Base):
    """送货单号每日计数器。

    Columns:
        date_ymd (String(8) PK): 自然日 YYYYMMDD
        last_value (Integer NOT NULL): 当日已发放的最大 NN（下一个 NN=last_value+1）
        created_at / updated_at (DateTime NOT NULL)
    """

    __tablename__ = "t_delivery_note_counter"

    date_ymd: Mapped[str] = mapped_column(
        String(8), primary_key=True,
        comment="自然日，格式 YYYYMMDD",
    )
    last_value: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
        comment="已发放的最大 NN",
    )
    created_at: Mapped["datetime | None"] = mapped_column(
        DateTime, nullable=True, server_default="now()",
    )
    updated_at: Mapped["datetime | None"] = mapped_column(
        DateTime, nullable=True, server_default="now()",
    )
