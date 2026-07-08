"""cnc_program_table: 新增 t_cnc_program 表（DDL 部分）

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-07-06

说明：
- 仅 DDL：t_cnc_program 表 + 3 索引。
  * `part_id` 普通列 + 普通索引（DB 层无物理外键，遵守项目约定 1）。
  * `file_type` 用大写扩展名（NC / TAP / CNC / MPF / NGC），与 t_drawing_file 一致。
- 种子部分（菜单 / clerk / cncprog 用户 / role_menu）已拆到
  `dev_data/000000000007_dev_cnc_seed.py`。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "000000000002"
down_revision: Union[str, None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    op.create_table(
        "t_cnc_program",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("part_id", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False,
                  comment="G 代码扩展名大写（NC / TAP / CNC / MPF / NGC）"),
        sa.Column("object_key", sa.String(length=500), nullable=False,
                  comment="COS 对象 key"),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("upload_status", sa.String(length=20), nullable=False,
                  server_default="READY",
                  comment="PENDING / READY / FAILED"),
        # AuditMixin
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_cnc_program_part", "t_cnc_program", ["part_id"])
    op.create_index("ix_t_cnc_program_part_type", "t_cnc_program", ["part_id", "file_type"])
    op.create_index("ix_t_cnc_program_created_at", "t_cnc_program", ["created_at"])


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    op.drop_index("ix_t_cnc_program_part_type", table_name="t_cnc_program")
    op.drop_index("ix_t_cnc_program_part", table_name="t_cnc_program")
    op.drop_index("ix_t_cnc_program_created_at", table_name="t_cnc_program")
    op.drop_table("t_cnc_program")
