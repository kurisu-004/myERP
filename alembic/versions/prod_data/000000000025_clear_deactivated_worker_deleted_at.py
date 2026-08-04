"""清空已停用工人的 deleted_at，修复列表不可见问题

历史 deactivate 把 deleted_at 也写了，导致已停用工人被 list 接口的软删守卫隐藏；
本迁移清空 `is_active=false AND deleted_at IS NOT NULL` 行的 deleted_at，
配合代码侧拆分 is_active / deleted_at 语义。

Revision ID: 000000000025
Revises: 000000000024
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000025"
down_revision: Union[str, None] = "000000000024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE t_worker SET deleted_at = NULL WHERE is_active = false AND deleted_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    """数据修复不可逆：原 deleted_at 值无法恢复。"""
    pass
