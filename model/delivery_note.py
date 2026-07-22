"""送货单聚合根 (t_delivery_note, 2026-07-22 新增).

完整状态机：DRAFT → SUBMITTED → PICKED_UP → ARCHIVED；SUBMITTED 可撤回 → DRAFT。
零件 ↔ 送货单多对一：`t_part.delivery_note_id` 是逻辑外键（CLAUDE.md §1）。
文员草拟 → 提交（待送货）→ 司机按图纸条码逐件扫描领取 → 归档；
单号 `DN-YYYYMMDD-NNNN` 由 `t_delivery_note_counter` 每日原子递增发放。

不在 DB 层加物理外键；状态合法性由 Python `DeliveryNoteStatus` 在 service / 状态机校验。
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id

if TYPE_CHECKING:
    pass  # 仅逻辑外键，不声明 relationship


class TDeliveryNote(Base, AuditMixin):
    """送货单聚合根。

    Columns:
        id (BigInteger PK): 雪花 ID
        delivery_note_no (String(16) NOT NULL): 单号 DN-YYYYMMDD-NNNN；部分唯一
            （`uq_t_delivery_note_no_active`）：deleted_at IS NULL 时唯一
        customer_id (BigInteger NOT NULL): 逻辑 FK → t_customer.id（叶子二级）
        status (String(16) NOT NULL): DRAFT / SUBMITTED / PICKED_UP / ARCHIVED
        submitted_at / picked_up_at (DateTime NULL)
        submitted_by / picked_up_by (BigInteger NULL): t_user.id
        driver_worker_id (BigInteger NULL): 司机 t_worker.id；service 校验
            `worker.work_type.code == '送货司机'` 且 is_active
        note (String(500) NULL)
        (AuditMixin: version + created_at/by + updated_at/by + deleted_at)
    """

    __tablename__ = "t_delivery_note"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    delivery_note_no: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="DN-YYYYMMDD-NNNN；唯一约束见 uq_t_delivery_note_no_active",
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True,
        comment="逻辑外键 → t_customer.id",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DRAFT",
        comment="DRAFT / SUBMITTED / PICKED_UP / ARCHIVED",
    )

    submitted_at: Mapped["datetime | None"] = mapped_column(
        DateTime, nullable=True, comment="提交审核时间",
    )
    picked_up_at: Mapped["datetime | None"] = mapped_column(
        DateTime, nullable=True, comment="司机领取时间",
    )

    submitted_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="提交人 t_user.id（MANAGER / CLERK）",
    )
    picked_up_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="领取时登入账号 t_user.id",
    )
    driver_worker_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="司机 t_worker.id；service 校验 work_type.code='送货司机' & is_active",
    )
    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="备注",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','PICKED_UP','ARCHIVED')",
            name="ck_t_delivery_note_status",
        ),
    )

    @property
    def sm(self) -> "DeliveryNoteStateMachine":
        """返回该送货单的状态机实例。"""
        from statemachines.delivery_note import DeliveryNoteStateMachine
        return DeliveryNoteStateMachine(model=self)
