"""add paired_file_id to t_part_file for G_CODE/SETUP_SHEET pairing (2026-07-24)

- Add nullable BigInteger column `paired_file_id` to t_part_file
  (G_CODE <-> SETUP_SHEET 双向关联；NULL = 未配对)
- Rebuild uk_t_part_file_single partial unique index WITHOUT SETUP_SHEET
  (SETUP_SHEET 改为允许多版本，与 G_CODE 一样)
- 向后兼容：旧行 paired_file_id 为 NULL，前端展示时独立成行不配对

接在 000000000014 之后，保持单 head 线性拓扑。

Revision ID: 000000000015
Revises: 000000000014
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000015"
down_revision: Union[str, None] = "000000000014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 添加 paired_file_id 列（nullable，无默认值）
    op.add_column(
        "t_part_file",
        sa.Column(
            "paired_file_id",
            sa.BigInteger(),
            nullable=True,
            comment="关联的配对文件ID（G_CODE <-> SETUP_SHEET 双向关联）",
        ),
    )

    # 2) 重建 uk_t_part_file_single 部分唯一索引：移除 SETUP_SHEET
    op.drop_index(
        "uk_t_part_file_single",
        table_name="t_part_file",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND "
            "kind IN ('DRAWING','3D_MODEL','SETUP_SHEET','ASSEMBLY_MASTER','CAD_2D')"
        ),
    )
    op.create_index(
        "uk_t_part_file_single",
        "t_part_file",
        ["part_id", "kind"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND "
            "kind IN ('DRAWING','3D_MODEL','ASSEMBLY_MASTER','CAD_2D')"
        ),
    )


def downgrade() -> None:
    # 1) 恢复 uk_t_part_file_single（恢复 SETUP_SHEET 到单文件约束）
    op.drop_index(
        "uk_t_part_file_single",
        table_name="t_part_file",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND "
            "kind IN ('DRAWING','3D_MODEL','ASSEMBLY_MASTER','CAD_2D')"
        ),
    )
    op.create_index(
        "uk_t_part_file_single",
        "t_part_file",
        ["part_id", "kind"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND "
            "kind IN ('DRAWING','3D_MODEL','SETUP_SHEET','ASSEMBLY_MASTER','CAD_2D')"
        ),
    )

    # 2) 移除 paired_file_id 列
    op.drop_column("t_part_file", "paired_file_id")
