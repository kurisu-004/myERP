"""hide assemblies_list menu (merged into parts list)

2026-07-30：装配件一览并入零件一览（GET /parts?include_assemblies=true），
assemblies_list 菜单下线。保留 AssemblyDetail 详情页与 /assemblies/:id 路由。

本迁移仅停用 t_menu 行（is_active=false），不删行、不动 role_menu，
downgrade 可一键恢复。

Revision ID: 000000000023
Revises: 000000000022
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000023"
down_revision: Union[str, None] = "000000000022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """停用 assemblies_list 菜单。"""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE t_menu
            SET is_active = false, updated_at = now()
            WHERE code = 'assemblies_list'
              AND is_active = true
              AND deleted_at IS NULL
            """
        )
    )


def downgrade() -> None:
    """恢复 assemblies_list 菜单。"""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE t_menu
            SET is_active = true, updated_at = now()
            WHERE code = 'assemblies_list'
              AND is_active = false
              AND deleted_at IS NULL
            """
        )
    )
