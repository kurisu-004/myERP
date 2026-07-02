"""装配件 ORM。

`t_assembly` 表示一份总装图对应的整套零件的逻辑分组，与 `t_part`
的子件通过 `t_part.assembly_id` 关联。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TAssembly(Base, AuditMixin):
    """装配件（总装图对应的逻辑实体）。

    与 `t_part` 的关系：
    - 装配件描述"这一整套东西要装起来"，挂总图 PDF + 客户 + 计划交期；
    - `t_part` 的子件行通过 `assembly_id` 反向挂到这里；
    - 装配件自身**不**走 `t_part` 的 8 态状态机，而是 4 态：
        PENDING → IN_PROCESS → COMPLETED
                          └─→ CANCELLED
      其中：
      - PENDING → IN_PROCESS 由"任一子件进入生产/品检/待送货/已送货"
        事件触发（service 层自动维护）。
      - IN_PROCESS → COMPLETED 由"所有子件都 COMPLETED"事件触发。
      - * → CANCELLED 走显式 `POST /assemblies/{id}/cancel` 端点，
        事务内把未完成的子件一同置 CANCELLED（保留审计事件）。
      - 终态（COMPLETED / CANCELLED）不接受再变更。

    注意：
    - 项目约定 **不在 DB 层加物理外键**；`customer_id` 是逻辑外键 → `t_customer.id`，
      是否为叶子节点、是否存在由 service 层校验。
    - 审计字段由 `AuditMixin` 提供，本类不重复声明。
    - 状态字段是 `varchar(20)`，取值合法性由 Python `AssemblyStatus` 在
      service 层校验。
    """

    __tablename__ = "t_assembly"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    drawing_no: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="总图图号（如 E42FX1020107101）",
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="装配体名称（如 精研挡料座）",
    )
    applicant_name: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    # 逻辑外键 → t_customer.id 叶子节点；service 层校验。
    customer_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )

    request_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    planned_delivery_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True
    )
    actual_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    is_urgent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
        index=True,
        comment="PENDING（默认）/ IN_PROCESS / COMPLETED / CANCELLED",
    )

    @property
    def sm(self) -> "AssemblyStateMachine":
        """返回此装配体的状态机实例。"""
        from statemachines.assembly import AssemblyStateMachine

        return AssemblyStateMachine(model=self)

    __table_args__ = (
        Index("ix_t_assembly_customer_status", "customer_id", "status"),
        Index("ix_t_assembly_planned_delivery", "planned_delivery_date"),
    )