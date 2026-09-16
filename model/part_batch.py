"""数量批次：工单（t_part）的数量载体与状态机载体。

设计要点（2026-07-29，父子批次方案）：
- 每个工单创建时自动生成 batch_no=1 的根批次（quantity=工单总量），
  **所有数量永远活在批次里**；不变量 Σ(活跃批次.quantity) = t_part.quantity
  由 service 层强制（拆分 / 取消 / 流转都经过 service）。
- 拆分：源批次 quantity -= n，新批次 quantity = n，并继承源批次的
  status / location / current_holder_id；`parent_batch_id` 记录拆分谱系
  （新批次 → 源批次）。
- 状态机：本表字段名与 TPart 的报工字段同名（status / location /
  current_holder_id），可直接复用
  `statemachines/part.py::PartStateMachine`（鸭子类型，SM 不感知批次）。
- **批次不设独立条码列**：v1 扫码统一用工单 `serial_no`，请求体携带
  `batch_id` 定位批次；前端展示用 `batch_label = f"{serial}B{batch_no:02d}"`
  （响应 schema 层计算，不落库）。serial 在工单全部终态后释放回池，
  因此批次码必须保持可派生、不持久化，避免撞码。
- 无物理外键（CLAUDE.md §1）：part_id / current_holder_id /
  delivery_note_id / parent_batch_id / current_process_step_id 均为逻辑外键，
  存在性由 service 层校验。

2026-09-16 PR-3（Rust 迁移 028）：
- 删除 ``next_process_id`` 列（原指向 `t_process.id`）与
  ``placed_at`` 列（首次进入 ON_SHELF 时间）。
- 新增 ``current_process_step_id`` 列（BigInteger NULL），逻辑外键 →
  ``t_process_chain_step.id``：把「下一步工序」语义从直接 process 改为工艺链
  上的具体 step，未来支持分支 / 多 step 共享同一 process 时无需再加列。
- ``has_been_repaired`` 已在 PR-2 删除（t_part 同步）。
- 业务含义切换（与 Rust 端同步）：
  - part 级 ``next_process_id`` 仍保留作为 rollup 物化列，由 Rust 端从
    「最落后」批次的 ``current_process_step.process_id`` 派生维护。
  - MCP 输出 ``batches[].next_process_name`` 由
    ``batch.current_process_step_id → TProcessChainStep.process_id → TProcess.name``
    派生（详见 ``service/mcp_query.py``）。
- 不动 alembic 迁移（PR-2 已确立双链并存惯例；表结构变更由 Rust 端迁移 028 主导）。
"""
from typing import Optional

from sqlalchemy import (
    BigInteger,
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
    # 2026-09-16 PR-3 删除 `next_process_id` 列（原 → t_process.id），
    # 改为 `current_process_step_id`（→ t_process_chain_step.id），
    # 由 MCP 等读端 JOIN t_process_chain_step 取 process_id 再查 t_process.name。
    current_process_step_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True,
        comment=(
            "2026-09-16 PR-3：逻辑外键 → t_process_chain_step.id；"
            "本批次在工艺链上的当前 step（替代原 t_part_batch.next_process_id → t_process.id）。"
            "part 级 ``t_part.next_process_id`` 仍保留作 rollup 物化列，"
            "由 Rust 侧从 min-progress 批次的 step.process_id 派生。"
        ),
    )
    # 2026-09-16 PR-3 删除 `placed_at` 列（首次进入 ON_SHELF 时间）。
    # 看板「压了多久」统计在 v2 端改为查 t_part_event 中 PLACED_ON_SHELF 事件
    # 的 created_at（v1 看板已 dormant）。

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

    # 2026-09-16 删除 `has_been_repaired`（PR-2 已删，t_part 同步删除）：
    # 返修标识无法归属到具体批次（拆分后新旧批次语义不清），整体废弃；
    # t_part.has_been_repaired 同步删除。v2 侧如需返修轨迹请查 t_part_event。

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
        # 2026-09-16 PR-3 删除 ix_t_part_batch_location_status_next_process
        # （依赖已删的 next_process_id 列）；v1 扫码台已 dormant，v2 侧
        # 按 location+status+holder 走新索引。
    )