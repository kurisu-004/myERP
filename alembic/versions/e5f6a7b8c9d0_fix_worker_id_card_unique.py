"""fix t_worker.id_card_no unique index

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-29

之前的迁移 d4e5f6a7b8c9 把唯一约束建在 `(id, id_card_no)` 联合上，
但 id 是 PK（始终不同），组合永远不同 → 实际上没起到约束作用，
两个工人可以塞同一个身份证号。

本迁移把索引换成 `UNIQUE INDEX (id_card_no) WHERE id_card_no IS NOT NULL`，
效果：
- 非空 id_card_no 全局唯一（一人一证）
- 多个 NULL id_card_no 允许共存（未填身份证的工人）
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uk_t_worker_id_idcard", table_name="t_worker")
    op.create_index(
        "uk_t_worker_id_card_no",
        "t_worker",
        ["id_card_no"],
        unique=True,
        postgresql_where="id_card_no IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uk_t_worker_id_card_no", table_name="t_worker")
    op.create_index(
        "uk_t_worker_id_idcard",
        "t_worker",
        ["id", "id_card_no"],
        unique=True,
    )