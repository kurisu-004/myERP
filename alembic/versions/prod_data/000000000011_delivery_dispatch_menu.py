"""add 送货 (delivery dispatch) menu granted to MANAGER + INSPECTOR

2026-07-23：新增司机送货扫码台入口菜单「送货」，挂在 order_group 下。

- code=delivery_dispatch，path=/delivery-dispatch（全屏司机扫码流程，
  MainLayout 之外）；点击进入工牌识别 → 只有「送货司机」工种可通过 →
  待送货单选择 → 逐件扫描 → 确认送货（复用后端 pickup-scan / pickup）。
- 权限：MANAGER + INSPECTOR。
- 详情页原「扫码领取」入口与报工台 DELIVER 路径同步移除（前端改动）。

幂等：
- t_menu：ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
- t_role_menu：ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING

Revision ID: 000000000011
Revises: 000000000010
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000011"
# 单 head 线性拓扑：… 009 → 010 → 011
down_revision: Union[str, None] = "000000000010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MENU_CODE = "delivery_dispatch"
_MENU_TITLE = "送货"
_MENU_PATH = "/delivery-dispatch"
_MENU_ICON = "Van"
_PARENT_CODE = "order_group"
_SORT_ORDER = 60
_ROLES = ("MANAGER", "INSPECTOR")


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
              AND role IN ('MANAGER', 'INSPECTOR')
              AND deleted_at IS NULL
            """
        ),
        {"mid": int(mid)},
    )
    bind.execute(
        sa.text("UPDATE t_menu SET deleted_at = now() WHERE id = :mid"),
        {"mid": int(mid)},
    )
