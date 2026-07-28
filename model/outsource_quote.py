"""外协报价单（t_outsource_quote，2026-07-16 新增；2026-07-29 升级为外协统一事实表）。

PR-H 2026-07-29：t_outsource_quote 不再只是"报价审批流"，而是外协全生命周期的
统一事实表。对账页直接查这张表，不再从 t_part_event 派生。

生命周期：
- 预发送（审批流）：DRAFT → SUBMITTED → APPROVED/REJECTED
- 发送后：APPROVED → OUTSOURCING → RECEIVED → BILLED
- 兼容：旧 USED 状态保留（数据迁移期），新流程不再产生 USED

发送（service/part.py::send_to_outsource）：
- APPROVAL 路径：现有 APPROVED 报价 mark_outsourcing → OUTSOURCING + 写 sent_at
- DIRECT 路径：service 自动创建一条 price=0 status=APPROVED 报价，再 mark_outsourcing
- DIRECT 报价默认单价 0，由文员/经理后填（对账页双击编辑）

接收（service/part.py::receive_from_outsource）：
- 反查 part+company+process 的 OUTSOURCING 报价 → mark_received + 写 received_at
- 状态机保证不会重复接收

对账（service/outsource_quote.py::reconcile_update_quote）：
- 允许 OUTSOURCING / RECEIVED 状态修改 unit_price / quantity / is_billed
- 勾 is_billed 触发 mark_billed（RECEIVED → BILLED）或 reopen_billed（BILLED → RECEIVED）

AuditMixin 提供乐观锁 `version` + 5 审计字段（CLAUDE.md §2 / §21）。
同一 (part, company, process) 仅允许一条非 REJECTED 活跃行（DB partial unique）。
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
    """外协报价单（外协全生命周期统一事实表）。

    Columns:
        id (BigInteger PK): 雪花 ID
        part_id (BigInteger): 逻辑外键 → t_part.id
        outsource_company_id (BigInteger): 逻辑外键 → t_outsource_company.id
        process_id (BigInteger): 逻辑外键 → t_process.id（必须 category=OUTSOURCE）
        price (Numeric(12,2)): 单件单价（CNY）；> 0
        note (String(500) NULL): CLERK 录入备注
        status (String(16)):
            DRAFT / SUBMITTED / APPROVED / REJECTED
            OUTSOURCING / RECEIVED / BILLED  (PR-H 2026-07-29 新加)
            USED                                (历史数据保留)
        submitted_at (DateTime NULL): 提交审核时间
        reviewed_at (DateTime NULL): 审批时间
        review_note (String(500) NULL): 审批意见（reject 时必填）
        sent_at (DateTime NULL): 发送时间（PR-H 2026-07-29 新加）
        received_at (DateTime NULL): 接收时间（PR-H 2026-07-29 新加）
        quantity (Integer NULL): 本次发送数量 snapshot（PR-H 2026-07-29 新加）
        is_billed (Boolean NOT NULL DEFAULT false): 对账标记
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
        comment="DRAFT/SUBMITTED/APPROVED/REJECTED/OUTSOURCING/RECEIVED/BILLED/USED",
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
    # PR-H 2026-07-29：外协对账字段（直接由 t_outsource_quote 承担）
    sent_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="发送时间（send_to_outsource 触发时写入）",
    )
    received_at: Mapped["DateTime | None"] = mapped_column(
        DateTime, nullable=True, comment="接收时间（receive_from_outsource 触发时写入）",
    )
    quantity: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次发送数量 snapshot；可能与 t_part.quantity 不同",
    )
    is_billed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
        comment="对账标记（与状态 RECEIVED/BILLED 配套）",
    )

    __table_args__ = (
        CheckConstraint("price > 0", name="ck_t_outsource_quote_price_positive"),
        CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED',"
            "'OUTSOURCING','RECEIVED','BILLED','USED')",
            name="ck_t_outsource_quote_status",
        ),
        # 同一 (part, company, process) 仅允许一条非 REJECTED 活跃行
        Index(
            "uq_t_outsource_quote_active_tuple",
            "part_id", "outsource_company_id", "process_id",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND status IN "
                "('DRAFT','SUBMITTED','APPROVED','OUTSOURCING','RECEIVED','BILLED','USED')"
            ),
        ),
        Index("ix_t_outsource_quote_status", "status"),
    )

    @property
    def sm(self) -> "OutsourceQuoteStateMachine":
        """返回此报价单的状态机实例。"""
        from statemachines.outsource_quote import OutsourceQuoteStateMachine
        return OutsourceQuoteStateMachine(model=self)
