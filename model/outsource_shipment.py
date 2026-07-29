"""外协发货记录 (t_outsource_shipment，2026-07-30 新增)。

每条记录 = 一次批次级发货（可拆可收）。
- 发送：创建 status=OUTSOURCING 行，quantity=发送量，unit_price=报价快照。
- 全量接收：status → RECEIVED，写 received_at。
- 部分接收：源 shipment 减量（剩 0 则 RECEIVED），另插新 shipment=RECEIVED。
- 取消：批次 cancel 时对应开口 shipment → CANCELLED。

AuditMixin 提供乐观锁 `version` + 5 审计字段。
不在 DB 层加物理外键（CLAUDE.md §1）。
"""
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id

if TYPE_CHECKING:
    pass


class TOutsourceShipment(Base, AuditMixin):
    """外协发货记录。

    Columns:
        id (BigInteger PK): 雪花 ID
        quote_id (BigInteger): 逻辑外键 → t_outsource_quote.id（快照来源报价）
        part_id (BigInteger): 逻辑外键 → t_part.id
        batch_id (BigInteger NULL): 逻辑外键 → t_part_batch.id；历史迁移行可能 NULL
        outsource_company_id (BigInteger): 逻辑外键 → t_outsource_company.id
        process_id (BigInteger): 逻辑外键 → t_process.id
        quantity (Integer NOT NULL): 本次发货数量
        unit_price (Numeric(12,2)): 发送时从报价快照的单价
        status (String(16)): OUTSOURCING / RECEIVED / CANCELLED
        sent_at (DateTime): 发送时间
        received_at (DateTime NULL): 接收时间
        is_billed (Boolean NOT NULL DEFAULT false): 对账标记
        (audit 6 fields via AuditMixin)
    """

    __tablename__ = "t_outsource_shipment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)
    quote_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="报价 id（逻辑 FK）",
    )
    part_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="零件 id（逻辑 FK）",
    )
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="批次 id（逻辑 FK）；历史迁移可能 NULL",
    )
    outsource_company_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="外协公司 id（逻辑 FK）",
    )
    process_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="工序 id（逻辑 FK）",
    )
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本次发货数量",
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, comment="发送时快照单价",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OUTSOURCING",
        comment="OUTSOURCING / RECEIVED / CANCELLED",
    )
    sent_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=False, comment="发送时间",
    )
    received_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="接收时间",
    )
    is_billed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), comment="对账标记",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OUTSOURCING','RECEIVED','CANCELLED')",
            name="ck_t_outsource_shipment_status",
        ),
        CheckConstraint("quantity > 0", name="ck_t_outsource_shipment_quantity_positive"),
        # 同一批次只能有一条未删除的开口 shipment（OUTSOURCING）
        Index(
            "uq_t_outsource_shipment_open_batch",
            "batch_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status='OUTSOURCING'"),
        ),
        Index("ix_t_outsource_shipment_status", "status"),
    )
