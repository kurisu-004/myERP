"""数量批次：工单（t_part）的数量载体与状态机载体。

设计要点（2026-07-29，父子批次方案）：
- 每个工单创建时自动生成 batch_no=1 的根批次（quantity=工单总量），
  **所有数量永远活在批次里**；不变量 Σ(活跃批次.quantity) = t_part.quantity
  由 service 层强制（拆分 / 取消 / 流转都经过 service）。
- 拆分：源批次 quantity -= n，新批次 quantity = n，并继承源批次的
  status / location / current_holder_id / next_process_id / placed_at；
  `parent_batch_id` 记录拆分谱系（新批次 → 源批次）。
- 状态机：本表字段名与 TPart 的报工字段同名（status / location /
  current_holder_id / next_process_id / placed_at），可直接复用
  `statemachines/part.py::PartStateMachine`（鸭子类型，SM 不感知批次）。
- **批次不设独立条码列**：v1 扫码统一用工单 `serial_no`，请求体携带
  `batch_id` 定位批次；前端展示用 `batch_label = f"{serial}B{batch_no:02d}"`
  （响应 schema 层计算，不落库）。serial 在工单全部终态后释放回池，
  因此批次码必须保持可派生、不持久化，避免撞码。
- 无物理外键（CLAUDE.md §1）：part_id / current_holder_id /
  next_process_id / delivery_note_id / parent_batch_id 均为逻辑外键，
  存在性由 service 层校验。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TPartBatch(Base, AuditMixin):
    """零件数量批次。一条记录 = 某工单在某个状态/位置下的一批数量。"""

    __tablename__ = "t_part_batch"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # 逻辑外键 → t_part.id（所属工单）
    part_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    # 工单内批次序号（1 起单调递增；拆分取 max+1，service 层在拆分锁内分配）
    batch_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 本批次数量（> 0；拆分会扣减源批次）
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # —— 报工字段（与 TPart 同名，状态机直接复用）——
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
        index=True,
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True,
        comment="OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF / OUTSOURCE_COMPANY",
    )
    # 多态 holder：t_shelf.id / t_worker.id / t_outsource_company.id（语义随 status）
    current_holder_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    next_process_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True,
        comment="逻辑外键 → t_process.id；place_on_shelf / RETURNED 时更新",
    )
    placed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True,
        comment="批次首次进入 ON_SHELF 的时间（看板「已放置 X 分钟」）",
    )

    # —— 送货单 ——
    # 批次级送货：一张 active 送货单至多挂一个批次；PICKED_UP 后保留指向归档单。
    delivery_note_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True,
        comment="逻辑外键 → t_delivery_note.id",
    )

    # —— 拆分谱系 ——
    # 本批次由哪个批次拆出；根批次（batch_no=1）为 NULL。
    parent_batch_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="拆分谱系：源批次 id；根批次 NULL",
    )

    # —— 返修件标识（PR-M 2026-08-04）——
    # 与 t_part.has_been_repaired 同语义、同步写入；
    # 仅展示用，不参与列表过滤热点索引。
    has_been_repaired: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="本批次是否经历过返修（与 t_part.has_been_repaired 同步写入）",
    )
    # ^ comment 与 migration 025 一致

    @property
    def sm(self) -> "PartStateMachine":
        """返回此批次的状态机实例（复用 PartStateMachine）。"""
        from statemachines.part import PartStateMachine
        return PartStateMachine(model=self)

    __table_args__ = (
        # 批次永不物理删除（取消走 CANCELLED 终态），普通唯一约束即可
        UniqueConstraint(
            "part_id", "batch_no", name="uq_t_part_batch_part_no",
        ),
        # 按状态 + holder 查询（看板「按货架分组」/ 工人持有列表）
        Index(
            "ix_t_part_batch_status_holder",
            "status",
            "current_holder_id",
        ),
        # 扫码台 PICK_UP 列表热点过滤（同 t_part 的 ix_t_part_location_status_next_process）
        Index(
            "ix_t_part_batch_location_status_next_process",
            "location", "status", "next_process_id",
        ),
    )
