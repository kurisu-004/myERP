"""外协报价单（t_outsource_quote，2026-07-16 新增）。

一笔报价 = (part, outsource_company, process) 三元组的待审报价。

- CLERK 录入 → DRAFT；提交 → SUBMITTED；MANAGER 审批 → APPROVED/REJECTED。
- 零件发送外协成功后，service 自动把匹配的 APPROVED 报价 mark_used → USED。
- AuditMixin 提供乐观锁 `version` + 5 审计字段（CLAUDE.md §2 / §21）。
- 同一 (part, company, process) 仅允许一条非 REJECTED 活跃行（DB partial unique）。
- 不在 DB 层加物理外键（CLAUDE.md §1）。
"""
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id

if TYPE_CHECKING:
    pass  # no relationships declared (logical FK only)


class TOutsourceQuote(Base, AuditMixin):
    """外协报价单。

    Columns:
        id (BigInteger PK): 雪花 ID
        part_id (BigInteger): 逻辑外键 → t_part.id
        outsource_company_id (BigInteger): 逻辑外键 → t_outsource_company.id
        process_id (BigInteger): 逻辑外键 → t_process.id（必须 category=OUTSOURCE）
        price (Numeric(12,2)): 单件单价（CNY）；> 0
        note (String(500) NULL): CLERK 录入备注
        status (String(16)): DRAFT / SUBMITTED / APPROVED / REJECTED / USED
        submitted_at (DateTime NULL): 提交审核时间
        reviewed_at (DateTime NULL): 审批时间
        review_note (String(500) NULL): 审批意见（reject 时必填）
        (audit 6 fields via AuditMixin：version + created_at/by + updated_at/by + deleted_at)
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
        Numeric(12, 2), nullable=False, comment="单件单价（CNY）",
    )
    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="备注",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DRAFT",
        comment="DRAFT / SUBMITTED / APPROVED / REJECTED / USED",
    )
    submitted_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="提交审核时间",
    )
    reviewed_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="审批时间",
    )
    review_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="审批意见（reject 必填）",
    )

    __table_args__ = (
        CheckConstraint("price > 0", name="ck_t_outsource_quote_price_positive"),
        CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','USED')",
            name="ck_t_outsource_quote_status",
        ),
        # 同一 (part, company, process) 仅允许一条非 REJECTED 活跃行
        Index(
            "uq_t_outsource_quote_active_tuple",
            "part_id", "outsource_company_id", "process_id",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND status IN ('DRAFT','SUBMITTED','APPROVED','USED')"
            ),
        ),
        Index("ix_t_outsource_quote_status", "status"),
    )

    @property
    def sm(self) -> "OutsourceQuoteStateMachine":
        """返回此报价单的状态机实例。"""
        from statemachines.outsource_quote import OutsourceQuoteStateMachine
        return OutsourceQuoteStateMachine(model=self)
