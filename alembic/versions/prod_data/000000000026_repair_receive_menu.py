"""add 返修接收 (repair receive) menu granted to MANAGER + CLERK + INSPECTOR

2026-08-04 「返修接收」菜单（PR-M）：

- code=repair_receive，title=返修接收，path=/repair/receive，icon=Tools
- 父节点 order_group（已在 000000000002 seed）
- 权限：MANAGER + CLERK + INSPECTOR（品检员与文员等同权限，可点返修/完成返修）
- sort_order=55（在 50=inspection_pending 之后，为未来项留位）

幂等：
- t_menu：ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
- t_role_menu：ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING

Revision ID: 000000000026
Revises: 000000000025
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000026"
# 单 head 线性拓扑：… 024 → 025（加 has_been_repaired 列）→ 026（此迁移）
down_revision: Union[str, None] = "000000000025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MENU_CODE = "repair_receive"
_MENU_TITLE = "返修接收"
_MENU_PATH = "/repair/receive"
_MENU_ICON = "Tools"
_PARENT_CODE = "order_group"
_SORT_ORDER = 55
_ROLES = ("MANAGER", "CLERK", "INSPECTOR")


def upgrade() -> None:
    bind = op.get_bind()
    from utils.id_gen import new_id

    parent_id = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code=:c AND deleted_at IS NULL"
        ),
        {"c": _PARENT_CODE},
    ).scalar()

    bind.execute(
        sa.text(
            """
            INSERT INTO t_menu
              (id, parent_id, code, title, path, icon, sort_order, is_active,
               created_at, updated_at)
            VALUES
              (:id, :parent_id, :code, :title, :path, :icon, :sort_order, true,
               now(), now())
            ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {
            "id": new_id(),
            "parent_id": parent_id,
            "code": _MENU_CODE,
            "title": _MENU_TITLE,
            "path": _MENU_PATH,
            "icon": _MENU_ICON,
            "sort_order": _SORT_ORDER,
        },
    )

    mid = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code=:c AND deleted_at IS NULL"
        ),
        {"c": _MENU_CODE},
    ).scalar()
    if mid is None:
        return

    for role in _ROLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, :role, :menu_id, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "role": role, "menu_id": int(mid)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    mid = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code=:c AND deleted_at IS NULL"
        ),
        {"c": _MENU_CODE},
    ).scalar()
    if mid is None:
        return
    bind.execute(
        sa.text(
            """
            DELETE FROM t_role_menu
            WHERE menu_id = :mid
              AND role IN ('MANAGER', 'CLERK', 'INSPECTOR')
              AND deleted_at IS NULL
            """
        ),
        {"mid": int(mid)},
    )
    bind.execute(
        sa.text("UPDATE t_menu SET deleted_at = now() WHERE id = :mid"),
        {"mid": int(mid)},
    )
