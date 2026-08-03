"""装配件 ORM。

`t_assembly` 表示一份总装图对应的整套零件的逻辑分组，与 `t_part`
的子件通过 `t_part.assembly_id` 关联。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    Date,
    Index,
    Integer,
    String,
    text,
)
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
        comment=(
            "PENDING（默认）/ IN_PROCESS / INSPECTION / READY_TO_SHIP / "
            "DELIVERED / COMPLETED / CANCELLED（2026-08-03 扩 7 态，"
            "由 service/_assembly_rollup.py 从子件派生）"
        ),
    )
    # 装配体级别序列号（与 t_part.serial_no 同 String(8)）。
    # 老装配件此字段为 NULL；service 层 cancel / soft_delete 跳过 NULL。
    # 子件派生规则：f"{serial_no}-{i:02d}"，i ∈ [1..99]，例 L1067-01 .. L1067-99。
    serial_no: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        index=True,
        comment="装配体序列号；子件序列号 = '{serial_no}-{i:02d}' 派生",
    )

    # —— 2026-07-24 新增：装配件自身价格 + 送货单字段 ——
    # 业务约束（service/assembly.py::update_assembly 维护）：
    #   - total_price > 0 ⇒ 自动清零所有 active 子件的 unit_price/total_price；
    #   - PartService.update_part 对 part.assembly_id != NULL 且所属装配件 total_price > 0
    #     时拒绝 unit_price/total_price 修改（BIZ_PART_PRICE_LOCKED_BY_ASSEMBLY 400）。
    # 数量语义：装配体的"套数"（如 1 套装配件 = N 个零件）。零件的 quantity 是单套内件数。
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    unit_price: Mapped[Decimal] = mapped_column(
        DECIMAL(12, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    total_price: Mapped[Decimal] = mapped_column(
        DECIMAL(14, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    # —— 送货单字段（与 t_part.order_no / system_delivery_date / note 对齐）——
    order_no: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        index=True,
        comment="订单号（法拉/路达共用，由文员录入）",
    )
    system_delivery_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="订单方系统内部交期（仅打印送货单时用）",
    )
    note: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="备注（文员手填，送货单打印可见）",
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