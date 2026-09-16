"""工艺链 step 表（2026-09-16 PR-3 引入 Python 端读访问）。

Rust v2 后端（迁移 017 建表，迁移 026 翻 FK 方向）维护 `t_process_chain_step`
这张工艺链步骤子表；Python v1 后端共享同一 `myerp` 库，原本只需要在 MCP
只读接口里查它（2026-09-16 起 MCP 的 `batches[].next_process_name` 改为由
`TPartBatch.current_process_step_id → TProcessChainStep.process_id → TProcess.name`
派生），所以只暴露读端 ORM 字段，不引入 Python 端迁移 / 写入路径。

字段口径与 rust 端迁移 `20260911100001_017_create_process_chain_tables.sql` +
`20260916110000_026_part_process_chain_fk_flip.sql` 完全一致；Rust 端对表结构的
后续修改需同步审视 Python 端此模型。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from model.base import Base
from utils.id_gen import new_id


class TProcessChainStep(Base):
    """工艺链 step 行（只读 ORM）。

    1:N 挂在 `t_part_process_chain`（header）下，每行 = 工艺链上的一道工序。
    `process_id` 是逻辑外键 → `t_process.id`（无物理 FK，CLAUDE.md §1）。

    2026-09-16 PR-3：Python 侧仅 MCP 只读接口用本表；未引入 service / repository /
    API 写入路径，避免与 Rust 端写入逻辑漂移。
    """

    __tablename__ = "t_process_chain_step"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )

    # 逻辑外键 → t_part_process_chain.id；工艺链 header id
    chain_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    # 工艺链内步骤顺序（稀疏 10/20/30）
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    # 逻辑外键 → t_process.id；本步骤对应的工序
    process_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    # 预估工时（分钟，>= 0）
    estimated_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    # 乐观锁版本号；service 写入侧每次 UPDATE 自增
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )

    # —— 审计字段（与 Rust 侧 017 迁移对齐）——
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    __table_args__ = (
        # chain_id 索引：与 Rust 端 ix_chain_step_chain 对齐
        Index("ix_chain_step_chain", "chain_id"),
    )