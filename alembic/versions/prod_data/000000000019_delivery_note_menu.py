"""delivery_note_menu: 生成送货单菜单 + MANAGER/CLERK 角色关联

Revision ID: 000000000019
Revises: 000000000018
Create Date: 2026-07-10

要点（prod_data 类别 — dev + prod 都需要）：
- PR-B 2026-07-10 文员生成送货单页面入口。
- 新增 1 个菜单行 `delivery_notes_new`（挂 order_group，sort_order 30）。
- role_menu 两行：MANAGER + CLERK。
- ON CONFLICT 全部幂等。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000019"
down_revision: Union[str, None] = "000000000018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 父菜单 order_group id
    parent_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='order_group' AND deleted_at IS NULL"
        )
    ).fetchone()
    if parent_row is None:
        return
    parent_id = int(parent_row[0])

    # 2) 菜单行
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
            "code": "delivery_notes_new",
            "title": "生成送货单",
            "path": "/delivery-notes/new",
            "icon": "Document",
            "sort_order": 30,
        },
    )

    mid_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='delivery_notes_new' AND deleted_at IS NULL"
        )
    ).fetchone()
    if mid_row is None:
        return
    mid = int(mid_row[0])

    # 3) 角色菜单关联（MANAGER + CLERK）
    for role in ("MANAGER", "CLERK"):
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, :role, :menu_id, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "role": role, "menu_id": mid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM t_role_menu WHERE role IN ('MANAGER', 'CLERK') "
            "AND menu_id IN (SELECT id FROM t_menu "
            "                WHERE code='delivery_notes_new' "
            "                AND deleted_at IS NULL)"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM t_menu WHERE code='delivery_notes_new' "
            "AND deleted_at IS NULL"
        )
    )