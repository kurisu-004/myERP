"""外协报价单（t_outsource_quote，2026-07-16 新增；2026-07-30 升级）。

2026-07-30 重构：报价回归纯审批对象。
- 生命周期：DRAFT → SUBMITTED → APPROVED/REJECTED。
- APPROVED 稳定，可被该 (part_id, process_id) 的所有批次发送复用。
- 发送/接收不再改变报价状态；新表 t_outsource_shipment 记录每次发货。
- 新增 `is_direct`：DIRECT 免审批占位标记（price=0, status=APPROVED）。

旧 sent_at/received_at/quantity/is_billed 列及旧状态 CHECK 保留兼容存量行（docstring 注明 legacy）。

AuditMixin 提供乐观锁 `version` + 5 审计字段（CLAUDE.md §2 / §21）。
不在 DB 层加物理外键（CLAUDE.md §1）。
"""
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer,
    Numeric, String, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id

if TYPE_CHECKING:
    pass  # no relationships declared (logical FK only)


class TOutsourceQuote(Base, AuditMixin):
    """外协报价单（纯审批对象，2026-07-30 重构）。

    Columns:
        id (BigInteger PK): 雪花 ID
        part_id (BigInteger): 逻辑外键 → t_part.id
        outsource_company_id (BigInteger): 逻辑外键 → t_outsource_company.id
        process_id (BigInteger): 逻辑外键 → t_process.id（必须 category=OUTSOURCE）
        price (Numeric(12,2)): 单件单价（CNY）；>= 0（DIRECT 自动创建时以 0 占位）
        note (String(500) NULL): CLERK 录入备注
        status (String(16)): DRAFT / SUBMITTED / APPROVED / REJECTED / OUTSOURCING / RECEIVED / BILLED / USED
        is_direct (Boolean NOT NULL DEFAULT false): DIRECT 免审批占位标记
        submitted_at (DateTime NULL): 提交审核时间
        reviewed_at (DateTime NULL): 审批时间
        review_note (String(500) NULL): 审批意见（reject 时必填）
        # legacy 列（2026-07-30 前由 t_outsource_quote 承担发货记录，现保留兼容存量行）
        sent_at (DateTime NULL): [legacy] 发送时间
        received_at (DateTime NULL): [legacy] 接收时间
        quantity (Integer NULL): [legacy] 本次发送数量 snapshot
        is_billed (Boolean NOT NULL DEFAULT false): [legacy] 对账标记
        (audit 6 fields via AuditMixin)
    """

    __tablename__ = "t_outsource_quote"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    part_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="零件 id（逻辑 FK → t_part.id）",
    )
    outsource_company_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="外协公司 id（逻辑 FK）",
    )
    process_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="工序 id（必须 OUTSOURCE，逻辑 FK）",
    )
    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False,
        comment="单件单价（CNY）；DIRECT 自动创建时允许 0 占位",
    )
    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="备注",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DRAFT",
        comment="DRAFT/SUBMITTED/APPROVED/REJECTED/OUTSOURCING/RECEIVED/BILLED/USED",
    )
    is_direct: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="DIRECT 免审批占位标记（price=0, status=APPROVED）",
    )
    submitted_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="提交审核时间",
    )
    reviewed_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="审批时间",
    )
    review_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="审批意见（reject 时必填）",
    )
    # legacy 列（2026-07-30 前由 t_outsource_quote 承担发货记录，现保留兼容存量行）
    sent_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="[legacy] 发送时间",
    )
    received_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="[legacy] 接收时间",
    )
    quantity: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="[legacy] 本次发送数量 snapshot",
    )
    is_billed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="[legacy] 对账标记",
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_t_outsource_quote_price_positive"),
        CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED',"
            "'OUTSOURCING','RECEIVED','BILLED','USED')",
            name="ck_t_outsource_quote_status",
        ),
        # 2026-07-30：同 (part_id, process_id) 仅允许一条非 DIRECT 的 APPROVED 报价
        Index(
            "uq_t_outsource_quote_approved_part_process",
            "part_id", "process_id",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND status='APPROVED' AND is_direct=false"
            ),
        ),
        Index("ix_t_outsource_quote_status", "status"),
    )

    @property
    def sm(self) -> "OutsourceQuoteStateMachine":
        """返回此报价单的状态机实例。"""
        from statemachines.outsource_quote import OutsourceQuoteStateMachine
        return OutsourceQuoteStateMachine(model=self)
