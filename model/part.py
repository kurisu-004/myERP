from datetime import date, datetime
from decimal import Decimal
from typing import Optional

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
      `customer_id` / `assembly_id` / `current_holder_id` 是逻辑外键，
      是否存在、是否被删除、是否允许写入都在 service 层处理。
    - 审计字段由 `AuditMixin` 提供。
    - **不用 DB ENUM**（CLAUDE.md 待补 §9）：`status` 列是 `varchar(20)`，
      取值合法性由 Python `PartStatus` 在 service 层校验。

    多态 holder：
    - `current_holder_id` 同时承载 `t_worker.id` 与 `t_shelf.id`；
      含义由 `status` + service 层校验共同决定。
      - `status=IN_PROCESS` 且 holder 在 `t_shelf`(zone=PRODUCTION) → 在生产货架
      - `status=IN_PROCESS` 且 holder 在 `t_worker`(is_active) → 工人持有
      - `status=INSPECTION` 且 holder 在 `t_shelf`(zone=INSPECTION) → 在品检货架
      - 否则 NULL
    """

    __tablename__ = "t_part"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # —— 业务字段 ——
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

    location: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True,
        comment="零件物理位置: OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF",
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
    # current_holder_id：零件当前持有者；逻辑指向 t_worker.id 或 t_shelf.id。
    # - IN_PROCESS 时可指向生产货架（holder=shelf）或被工人持有（holder=worker）。
    # - INSPECTION 时指向品检货架。
    # 不在 DB 层加物理外键；service 层校验。
    current_holder_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # placed_at：文员首次把零件放到生产货架上的时间（PENDING→IN_PROCESS 时置位）。
    # 数据大屏「按货架分组」展示「已放置 X 分钟」。
    placed_at: Mapped[datetime | None] = mapped_column(
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

    # —— 装配字段 ——
    assembly_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="逻辑外键 → t_assembly.id；NULL = 非装配件子件",
    )

    # —— 工序字段 ——
    # 下一道工序：place_on_shelf 时必填；RETURNED 时由工人指定。
    # 逻辑外键 → t_process.id；service 层校验存在性。
    next_process_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="逻辑外键 → t_process.id；place_on_shelf / RETURNED 时更新",
    )

    @property
    def sm(self) -> "PartStateMachine":
        """返回此零件的状态机实例。"""
        from statemachines.part import PartStateMachine
        return PartStateMachine(model=self)

    # —— 组合索引 ——
    # `ix_t_part_status_holder`：按状态 + holder 查询（Dashboard「按货架分组」）。
    # `ix_t_part_customer_status_delivery`：按客户 + 状态 + 交期查询。
    # `ix_t_part_location_status_next_process`：扫码台 PICK_UP 列表热点过滤
    #   (status='IN_PROCESS' AND location='PRODUCTION_SHELF' AND current_holder_id=shelf
    #    AND next_process_id IN mapped_process_ids)
    __table_args__ = (
        Index(
            "ix_t_part_status_holder",
            "status",
            "current_holder_id",
        ),
        Index(
            "ix_t_part_customer_status_delivery",
            "customer_id",
            "status",
            "planned_delivery_date",
        ),
        Index("ix_t_part_assembly_id_status", "assembly_id", "status"),
        Index(
            "ix_t_part_location_status_next_process",
            "location", "status", "next_process_id",
        ),
    )
