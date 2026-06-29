from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    DECIMAL,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.audit import AuditMixin
from model.base import Base
from model.customer import TCustomer
from model.enums import PartStatus
from utils.id_gen import new_id


class TPart(Base, AuditMixin):
    """零件/订单明细记录。

    一条 TPart 对应 Excel 表中一行加工/生产明细：
        法拉"母排厂 林雪强 E42804FZJ076100 C3600672J003绝缘纸折弯工装 数量2 单价1800"
        路达"开发一部197 LT16681 非标螺纹塞规 数量1 单价90"

    ID 由雪花算法生成（见 utils.id_gen.new_id），
    通过 SQLAlchemy 的 default 钩子在 INSERT 前自动填充。

    注意：
    - 项目约定 **不在 DB 层加物理外键**。
      `customer_id` / `current_worker_id` 是逻辑外键，分别指向
      t_customer.id 和 t_worker.id，是否存在、是否被删除、是否允许
      写入都在 service 层处理。
    - 审计字段（created_at / created_by / updated_at / updated_by / deleted_at）
      由 `AuditMixin` 提供。
    - **不用 DB ENUM**（CLAUDE.md 待补 §9）：`status` 列是 `varchar(20)`，
      取值合法性由 Python `PartStatus` 在 service 层校验。
    """

    __tablename__ = "t_part"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # —— 业务字段 ——
    # 序列号：每客户独立循环，形式 "F1000" / "L1234"。
    # - 状态在 (PENDING, READY, IN_PROCESS, INSPECTION, READY_TO_SHIP,
    #   DELIVERED, REPAIRING) 时非空；
    # - 状态变为 COMPLETED / CANCELLED 时 service 层置 NULL → 释放回池。
    # 由 repository.find_next_serial_for_customer 在写入前分配。
    serial_no: Mapped[str | None] = mapped_column(
        String(8), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    drawing_no: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    applicant_name: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(
        DECIMAL(12, 2), nullable=False, default=Decimal("0")
    )
    total_price: Mapped[Decimal] = mapped_column(
        DECIMAL(14, 2), nullable=False, default=Decimal("0")
    )

    request_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    planned_delivery_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True
    )
    actual_delivery_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )

    # 订单状态。DB 存 varchar(20)，取值合法性由 Python PartStatus 校验。
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PartStatus.PENDING.value,
        server_default=PartStatus.PENDING.value,
        index=True,
    )

    is_urgent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
        comment="是否加急",
    )

    # —— 报工字段 ——
    # current_worker_id：IN_PROCESS 时持有该零件的工人；逻辑外键 → t_worker.id
    current_worker_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # released_at：文员点击"开始生产"的时间（PENDING → READY 时置位）；
    # 数据大屏"待加工队列"按此升序排序。
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    # —— 逻辑外键 —— 指向 t_customer.id 的叶子节点（具体分厂/部门）。
    customer_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    customer: Mapped["TCustomer"] = relationship(
        "TCustomer",
        primaryjoin="TPart.customer_id == TCustomer.id",
        foreign_keys="[TPart.customer_id]",
        back_populates="parts",
        lazy="raise",
    )

    # —— 组合索引：按客户+状态查按交期排序，是高频看板查询
    __table_args__ = (
        Index(
            "ix_t_part_customer_status_delivery",
            "customer_id",
            "status",
            "planned_delivery_date",
        ),
    )