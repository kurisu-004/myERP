"""add t_assembly pricing + delivery-note fields (2026-07-24)

装配件本身需可设置价格（数量 / 单价 / 总价），并对齐零件一览的
送货单字段（订单号 / 系统交期 / 备注），让装配件列表页与零件一览
列保持一致。

业务约束（service 层维护，详见 service/assembly.py::update_assembly）：
- 装配体设置总价 > 0 时，强制清零所有 active 子件的 unit_price/total_price；
- 装配体总价 = 0 时不做限制（子件可保留独立价格）。
- 父装配体已设总价时，子件调用 POST /parts/{id}/update 改价会被
  PartService.update_part 拒绝（BIZ_PART_PRICE_LOCKED_BY_ASSEMBLY）。

字段：
- quantity Integer NOT NULL DEFAULT 1
- unit_price DECIMAL(12,2) NOT NULL DEFAULT 0
- total_price DECIMAL(14,2) NOT NULL DEFAULT 0
- order_no String(30) NULL + ix_t_assembly_order_no
- system_delivery_date Date NULL
- note String(500) NULL

向后兼容：旧装配件这 6 个字段全为默认值；前端 null 显示 '-'。
接在 000000000013 之后，保持单 head 线性拓扑。

Revision ID: 000000000014
Revises: 000000000013
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000014"
# 单 head 线性拓扑：… 013 → 014
down_revision: Union[str, None] = "000000000013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_assembly",
        sa.Column(
            "quantity",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "t_assembly",
        sa.Column(
            "unit_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "t_assembly",
        sa.Column(
            "total_price",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "t_assembly",
        sa.Column("order_no", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "t_assembly",
        sa.Column("system_delivery_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "t_assembly",
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_t_assembly_order_no",
        "t_assembly",
        ["order_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_t_assembly_order_no", table_name="t_assembly")
    op.drop_column("t_assembly", "note")
    op.drop_column("t_assembly", "system_delivery_date")
    op.drop_column("t_assembly", "order_no")
    op.drop_column("t_assembly", "total_price")
    op.drop_column("t_assembly", "unit_price")
    op.drop_column("t_assembly", "quantity")