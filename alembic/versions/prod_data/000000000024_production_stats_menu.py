"""seed production statistics menu for MANAGER

新增顶级「生产统计」菜单（/statistics），仅授予 MANAGER 角色。
菜单排在「首页」之后；所有插入使用 partial unique index 对应的
ON CONFLICT DO NOTHING，确保迁移幂等。

Revision ID: 000000000024
Revises: 000000000023
Create Date: 2026-08-03 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from utils.id_gen import new_id


revision: str = "000000000024"
down_revision: Union[str, None] = "000000000023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MENU_CODE = "production_stats"


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            INSERT INTO t_menu
              (id, parent_id, code, title, path, icon, sort_order,
               created_at, updated_at)
            VALUES
              (:id, NULL, :code, '生产统计', '/statistics', 'DataAnalysis', 12,
               now(), now())
            ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": new_id(), "code": _MENU_CODE},
    )

    menu_id = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code = :code AND deleted_at IS NULL"
        ),
        {"code": _MENU_CODE},
    ).scalar_one()

    bind.execute(
        sa.text(
            """
            INSERT INTO t_role_menu
              (id, role, menu_id, created_at, updated_at)
            VALUES
              (:id, 'MANAGER', :menu_id, now(), now())
            ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": new_id(), "menu_id": int(menu_id)},
    )


def downgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            DELETE FROM t_role_menu
            WHERE role = 'MANAGER'
              AND menu_id IN (
                  SELECT id FROM t_menu
                  WHERE code = :code AND deleted_at IS NULL
              )
              AND deleted_at IS NULL
            """
        ),
        {"code": _MENU_CODE},
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM t_menu
            WHERE code = :code AND deleted_at IS NULL
            """
        ),
        {"code": _MENU_CODE},
    )
