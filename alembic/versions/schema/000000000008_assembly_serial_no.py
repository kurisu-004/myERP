"""assembly_serial_no: 给 t_assembly 增加 serial_no 列

Revision ID: 000000000008
Revises: 000000000007
Create Date: 2026-07-08

说明：
- 装配件的序列号（与 t_part.serial_no 同 String(8) 长度限制）。
- 装配件自身占用一个真实计数（L1067），
  其下子件派生为 L1067-01 / L1067-02 / ... / L1067-99（两位零填充）。
- 既存装配件（migration 之前已创建的）serial_no = NULL，
  service 层 cancel / delete 时判断 NULL 跳过。
- 部分唯一索引与 t_part.serial_no 同款，仅约束未软删且非 NULL 行。
- 不做老数据回填：新装配件从今往后自动分配。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000008"
down_revision: Union[str, None] = "000000000007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_assembly",
        sa.Column(
            "serial_no",
            sa.String(length=8),
            nullable=True,
            comment="装配体序列号；子件派生为 '{serial_no}-{i:02d}'",
        ),
    )
    op.create_index(
        "ix_t_assembly_serial_no", "t_assembly", ["serial_no"]
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uk_t_assembly_serial_no "
            "ON t_assembly (serial_no) "
            "WHERE deleted_at IS NULL AND serial_no IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uk_t_assembly_serial_no"))
    op.drop_index("ix_t_assembly_serial_no", table_name="t_assembly")
    op.drop_column("t_assembly", "serial_no")
