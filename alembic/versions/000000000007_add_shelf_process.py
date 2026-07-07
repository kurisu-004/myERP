"""add_shelf_process: 货架 ↔ 工序 多对多映射表

Revision ID: 000000000007
Revises: 000000000006
Create Date: 2026-07-07

说明：
- 新增 t_shelf_process junction 表：每个货架可配置可执行的工序。
- 无物理外键（项目约定），逻辑引用 t_shelf.id / t_process.id。
- 种子数据：所有已有 PRODUCTION 货架自动映射全部 INHOUSE 工序，
  保证存量兼容（工人放回时工序下拉框不为空）。
- 品检货架不自动映射工序（品检区不需要放回工序选择）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from utils.id_gen import new_id

revision: str = "000000000007"
down_revision: Union[str, None] = "000000000006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. t_shelf_process DDL
    # -------------------------------------------------------------------------
    op.create_table(
        "t_shelf_process",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("shelf_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_shelf.id"),
        sa.Column("process_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_process.id"),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default=sa.text("0"),
                  comment="工序在货架映射内的显示顺序"),
        # AuditMixin
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_shelf_process_shelf", "t_shelf_process", ["shelf_id"])
    op.create_index("ix_t_shelf_process_process", "t_shelf_process", ["process_id"])
    op.create_index("ix_t_shelf_process_deleted_at", "t_shelf_process", ["deleted_at"])

    # 部分唯一索引：同一货架下同一工序不能重复；软删后可重建
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uk_t_shelf_process
            ON t_shelf_process (shelf_id, process_id)
            WHERE deleted_at IS NULL
            """
        )
    )

    # Check constraint：防止 shelf_id == process_id（跨类型自引用防护）
    op.create_check_constraint(
        "ck_t_shelf_process_no_self_loop",
        "t_shelf_process",
        "shelf_id <> process_id",
    )

    # -------------------------------------------------------------------------
    # 2. 种子数据：每个 PRODUCTION 货架自动映射全部 INHOUSE 工序
    # -------------------------------------------------------------------------
    bind = op.get_bind()
    shelf_rows = bind.execute(
        sa.text(
            "SELECT id FROM t_shelf WHERE zone = 'PRODUCTION' AND deleted_at IS NULL"
        )
    ).fetchall()
    process_rows = bind.execute(
        sa.text(
            "SELECT id FROM t_process WHERE category = 'INHOUSE' AND deleted_at IS NULL ORDER BY sort_order, id"
        )
    ).fetchall()

    shelf_ids = [r[0] for r in shelf_rows]
    process_ids = [r[0] for r in process_rows]

    if shelf_ids and process_ids:
        for sid in shelf_ids:
            for idx, pid in enumerate(process_ids):
                bind.execute(
                    sa.text(
                        "INSERT INTO t_shelf_process (id, shelf_id, process_id, sort_order) "
                        "VALUES (:id, :shelf_id, :process_id, :sort_order)"
                    ),
                    {
                        "id": new_id(),
                        "shelf_id": sid,
                        "process_id": pid,
                        "sort_order": idx,
                    },
                )


def downgrade() -> None:
    op.drop_index("ix_t_shelf_process_deleted_at", table_name="t_shelf_process")
    op.drop_index("ix_t_shelf_process_process", table_name="t_shelf_process")
    op.drop_index("ix_t_shelf_process_shelf", table_name="t_shelf_process")
    op.drop_constraint(
        "ck_t_shelf_process_no_self_loop", "t_shelf_process", type_="check"
    )
    op.drop_table("t_shelf_process")
