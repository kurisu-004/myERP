"""add t_worker.id_card_no + phone, with composite unique (id, id_card_no)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-29

说明：
- 加 `id_card_no VARCHAR(18)` 和 `phone VARCHAR(20)` 两个可空字段。
- 加 `UNIQUE INDEX uk_t_worker_id_idcard ON t_worker (id, id_card_no)`。
  由于 `id` 已是主键（全局唯一），联合唯一索引实质上等价于
  "id_card_no 在非空范围内全局唯一"；空值（NULL）允许多条共存。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_worker",
        sa.Column("id_card_no", sa.String(length=18), nullable=True),
    )
    op.add_column(
        "t_worker",
        sa.Column("phone", sa.String(length=20), nullable=True),
    )
    # 联合唯一索引：(id, id_card_no)。id 已是 PK，组合上 id_card_no 也必须唯一。
    op.create_index(
        "uk_t_worker_id_idcard",
        "t_worker",
        ["id", "id_card_no"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uk_t_worker_id_idcard", table_name="t_worker")
    op.drop_column("t_worker", "phone")
    op.drop_column("t_worker", "id_card_no")