"""add t_delivery_note.delivery_date

2026-07-23 送货单管理页增强：
- 新建草稿弹框可指定送货日期（默认 = 创建当天）
- DRAFT / SUBMITTED 详情页可手动修改日期（partial update endpoint）
- 一览新增「送货日期」列
- 不影响既有 pickup / archive 流程（archived 行仍能打印，依 PR-G 的
  t_part.delivery_note_id 保留约定）

字段 nullable，旧草稿保持 NULL；前端展示 '—' 即可。

链在 schema/009 (PR-G t_delivery_note) 之后，保持 CLAUDE.md §Alembic 的
单 head 线性拓扑 001 → 003 → 005 → 009 → 010。

Revision ID: 000000000010
Revises: 000000000009
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000010"
down_revision: Union[str, None] = "000000000009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_delivery_note",
        sa.Column(
            "delivery_date",
            sa.Date(),
            nullable=True,
            comment="送货日期；默认 = 创建当天；DRAFT/SUBMITTED 可改",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_delivery_note", "delivery_date")
