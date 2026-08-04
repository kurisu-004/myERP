"""grant CNC_PROGRAMMER access to parts_list menu (2026-08-05)

PR-K 系列：六合一功能批次 — 任务 1（零件召回）前置。
让编程员（CNC_PROGRAMMER）能进入「零件一览」，与 INSPECTOR 同款只读权限
（看不到单价；可召回在架件为待编程）。

菜单 code `parts_list` 已存在于 t_menu（000000000002 seed），本迁移只补
role_menu 行。幂等：ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING。

Revision ID: 000000000030
Revises: 000000000029
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000030"
# 单 head 线性拓扑：… 028 → 029(schema) → 030(prod_data)
down_revision: Union[str, None] = "000000000029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# CNC_PROGRAMMER 新增可见的菜单 code 列表（菜单已存在，只需补 t_role_menu）。
_CNC_MENUS: list[str] = [
    "parts_list",
]


def upgrade() -> None:
    bind = op.get_bind()

    # 取出要授权的 menu_id（已存在于 000000000002 的 _MENUS）
    menu_rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": _CNC_MENUS},
    ).fetchall()
    id_by_code = {code: int(mid) for code, mid in menu_rows}

    # 与 000000000006 同款：snowflake id + ON CONFLICT 幂等
    from utils.id_gen import new_id

    for code in _CNC_MENUS:
        mid = id_by_code.get(code)
        if mid is None:
            # 极端情况：菜单 row 不存在（老库漏 seed），跳过；后续用户跑
            # 000000000002 数据迁移即可补齐。
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, :role, :menu_id, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "role": "CNC_PROGRAMMER", "menu_id": mid},
        )


def downgrade() -> None:
    # 回滚：移除 CNC_PROGRAMMER 在本迁移新增的 t_role_menu 行。
    bind = op.get_bind()
    menu_rows = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": _CNC_MENUS},
    ).fetchall()
    menu_ids = [int(r[0]) for r in menu_rows]
    if not menu_ids:
        return
    bind.execute(
        sa.text(
            """
            DELETE FROM t_role_menu
            WHERE role = 'CNC_PROGRAMMER'
              AND menu_id IN :menu_ids
              AND deleted_at IS NULL
            """
        ).bindparams(sa.bindparam("menu_ids", expanding=True)),
        {"menu_ids": menu_ids},
    )
