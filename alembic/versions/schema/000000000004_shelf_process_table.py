"""shelf_process_table: 新增 t_shelf_process junction 表（DDL 部分）

Revision ID: 000000000004
Revises: 000000000003
Create Date: 2026-07-07

说明：
- 仅 DDL：t_shelf_process 表 + 3 索引 + 部分唯一索引 + 防自环 check 约束。
- 无物理外键（项目约定），逻辑引用 t_shelf.id / t_process.id。
- 种子部分（每架 PRODUCTION 货架自动映射全部 INHOUSE 工序）已拆到
  `dev_data/000000000011_dev_shelf_process_seed.py`。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "000000000004"
down_revision: Union[str, None] = "000000000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
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


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    op.drop_index("ix_t_shelf_process_deleted_at", table_name="t_shelf_process")
    op.drop_index("ix_t_shelf_process_process", table_name="t_shelf_process")
    op.drop_index("ix_t_shelf_process_shelf", table_name="t_shelf_process")
    op.drop_constraint(
        "ck_t_shelf_process_no_self_loop", "t_shelf_process", type_="check"
    )
    op.drop_table("t_shelf_process")
