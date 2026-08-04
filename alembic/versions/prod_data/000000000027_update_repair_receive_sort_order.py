"""update repair_receive sort_order 55 → 65 (PR-M 2026-08-04 续)

用户要求「返修接收菜单显示在送货菜单下方」。
order_group 子树现状：
  50 inspection_pending / 60 delivery_dispatch / 55 repair_receive
调整后：... → 50 → 60 → 65 repair_receive → ?

幂等：op.execute 重复运行结果相同（重复 UPDATE 到同值）。

Revision ID: 000000000027
Revises: 000000000026
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op


revision: str = "000000000027"
# 单 head 线性拓扑：026 → 027
down_revision: Union[str, None] = "000000000026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE t_menu SET sort_order = 65 "
        "WHERE code = 'repair_receive' AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE t_menu SET sort_order = 55 "
        "WHERE code = 'repair_receive' AND deleted_at IS NULL"
    )
