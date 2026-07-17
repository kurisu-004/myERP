"""add t_part.order_no / system_delivery_date / note

PR-F（2026-07-17）：送货单打印功能要求新增三个可选字段。

- order_no：订单号（法拉示例「订单号」、路达示例「订单编号」共用）
- system_delivery_date：订单方系统内部交期（区别于我方 planned_delivery_date）
- note：备注（文员手填）

三个字段均为 nullable，向后兼容；旧零件保持 NULL。

Revision ID: 000000000005
Revises: 000000000001
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000005"
# 链在 schema/003 (外协报价表) 之后，保持 CLAUDE.md §15.1/§20 承诺的单 head 线性拓扑
# 001 → 002 → 003 → 005
down_revision: Union[str, None] = "000000000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_part",
        sa.Column("order_no", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "t_part",
        sa.Column("system_delivery_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "t_part",
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_t_part_order_no",
        "t_part",
        ["order_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_t_part_order_no", table_name="t_part")
    op.drop_column("t_part", "note")
    op.drop_column("t_part", "system_delivery_date")
    op.drop_column("t_part", "order_no")