from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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
      `customer_id` / `assembly_id` 是逻辑外键，
      是否存在、是否被删除、是否允许写入都在 service 层处理。
    - 审计字段由 `AuditMixin` 提供。
    - **不用 DB ENUM**（CLAUDE.md 待补 §9）：`status` 列是 `varchar(20)`，
      取值合法性由 Python `PartStatus` 在 service 层校验。

    2026-09-16 t_part 瘦身（Rust v2 迁移 027，与本模型同步删列）：
    - `t_part` 只表示工单，批次依附信息全部归 `t_part_batch`。以下六列已从
      DB 与本模型同步删除：`actual_delivery_date` / `location` /
      `current_holder_id` / `placed_at` / `delivery_note_id` / `has_been_repaired`
      （`has_been_repaired` 因无法归属到具体批次整体废弃，批次侧同删）。
    - 保留 `status` / `next_process_id` 作为 rollup 物化列（v2 仍维护）。
    - 位置 / 持有者 / 上架时间 / 送货单归属请查 `TPartBatch` 同名字段。
    - v1 业务端点已 dormant（2026-09-15 Phase 5 起前端走 v2），本模型只需
      保住 4 个打印端点与 /api/mcp 只读查询的 `select(TPart)` 不炸。
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
    # 2026-09-16 删除 `actual_delivery_date`（t_part 瘦身，Rust 迁移 027）：
    # 实际交付日期的批次归属在 v2 侧重新设计，工单级不再物化。

    # —— 送货单字段（PR-F 2026-07-17，三个可选字段）——
    # order_no：订单号（法拉示例「订单号」、路达示例「订单编号」共用）
    order_no: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        index=True,
        comment="订单号（法拉/路达共用，由文员录入）",
    )
    # system_delivery_date：订单方系统内部交期（区别于我方 planned_delivery_date）
    system_delivery_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="订单方系统内部交期（仅打印送货单时用）",
    )
    # note：备注（文员手填）
    note: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="备注（文员手填，送货单打印可见）",
    )

    # 订单状态。DB 存 varchar(20)，取值合法性由 Python PartStatus 校验。
    # 2026-09-16 保留：rollup 物化列（v2 按「最落后」活跃批次维护），瘦身后不删。
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PartStatus.PENDING.value,
        server_default=PartStatus.PENDING.value,
        index=True,
    )

    # 2026-09-16 删除 `location`（t_part 瘦身，Rust 迁移 027）：
    # 物理位置（OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF /
    # OUTSOURCE_COMPANY）是批次依附信息，归 `t_part_batch.location`。

    is_urgent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
        comment="是否加急",
    )

    # 2026-09-16 删除 `current_holder_id` / `placed_at`（t_part 瘦身，
    # Rust 迁移 027）：多态持有者（t_worker / t_shelf / t_outsource_company）
    # 与首次上架时间同为批次依附信息，归 `t_part_batch` 同名字段。

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

    # 2026-09-16 删除 `delivery_note_id`（t_part 瘦身，Rust 迁移 027）：
    # 送货单归属是批次级信息（一张单挂若干批次），归 `t_part_batch.delivery_note_id`。

    # —— 工序字段 ——
    # 下一道工序：place_on_shelf 时必填；RETURNED 时由工人指定。
    # 逻辑外键 → t_process.id；service 层校验存在性。
    # 2026-09-16 保留：rollup 物化列（v2 按「最落后」活跃批次维护），瘦身后不删。
    next_process_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="逻辑外键 → t_process.id；place_on_shelf / RETURNED 时更新",
    )

    # 2026-09-16 删除 `has_been_repaired`（t_part 瘦身，Rust 迁移 027）：
    # 返修标识无法归属到具体批次（拆分后新旧批次语义不清），整体废弃，
    # `t_part_batch.has_been_repaired` 同步删除。

    @property
    def sm(self) -> "PartStateMachine":
        """返回此零件的状态机实例。"""
        from statemachines.part import PartStateMachine
        return PartStateMachine(model=self)

    # —— 组合索引 ——
    # `ix_t_part_customer_status_delivery`：按客户 + 状态 + 交期查询。
    # 2026-09-16 t_part 瘦身（Rust 迁移 027）：随 `location` / `current_holder_id`
    # / `placed_at` / `delivery_note_id` 删列，以下索引一并删除（DB 侧由 Rust
    # 迁移 DROP，本模型同步去掉定义）：
    # - ix_t_part_status_holder(status, current_holder_id)
    # - ix_t_part_location_status_next_process(location, status, next_process_id)
    # - 列级 index=True 的 ix_t_part_location / ix_t_part_current_holder_id /
    #   ix_t_part_placed_at / ix_t_part_delivery_note_id（随列删除自动消失）
    __table_args__ = (
        Index(
            "ix_t_part_customer_status_delivery",
            "customer_id",
            "status",
            "planned_delivery_date",
        ),
        Index("ix_t_part_assembly_id_status", "assembly_id", "status"),
    )
