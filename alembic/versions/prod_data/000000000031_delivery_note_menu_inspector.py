"""grant INSPECTOR access to delivery_notes_manage menu (2026-08-05)

PR-K 系列：六合一功能批次 — 任务「送货单菜单与全部功能向 INSPECTOR 角色开放」。
让巡检员 (INSPECTOR) 能进入「送货管理」菜单，与 CLERK / MANAGER 享有同等视角。

菜单 code `delivery_notes_manage` 已存在于 t_menu（000000000009 在
schema_init / data_init 之间定义），本迁移只补 role_menu 行。
幂等：ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING。

后端 api/v1/delivery_note.py 已同步把 INSPECTOR 加入 _OFFICE_DEP / _OFFICE_ROLES，
与本菜单授权配套，避免「看见菜单但 403」。

Revision ID: 000000000031
Revises: 000000000030
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000031"
# 单 head 线性拓扑：… 029(schema) → 030(prod_data) → 031(prod_data)
down_revision: Union[str, None] = "000000000030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# INSPECTOR 新增可见的菜单 code 列表（菜单已存在，只需补 t_role_menu）。
_INSPECTOR_MENUS: list[str] = [
    "delivery_notes_manage",
]


def upgrade() -> None:
    bind = op.get_bind()

    # 取出要授权的 menu_id（已存在于 000000000009 的数据 seed）
    menu_rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": _INSPECTOR_MENUS},
    ).fetchall()
    id_by_code = {code: int(mid) for code, mid in menu_rows}

    # 与 000000000006 / 000000000030 同款：snowflake id + ON CONFLICT 幂等
    from utils.id_gen import new_id

    for code in _INSPECTOR_MENUS:
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
            {"id": new_id(), "role": "INSPECTOR", "menu_id": mid},
        )


def downgrade() -> None:
    # 回滚：只移除 INSPECTOR 在本迁移新增的 t_role_menu 行，不动 MANAGER/CLERK。
    bind = op.get_bind()
    menu_rows = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": _INSPECTOR_MENUS},
    ).fetchall()
    menu_ids = [int(r[0]) for r in menu_rows]
    if not menu_ids:
        return
    bind.execute(
        sa.text(
            """
            DELETE FROM t_role_menu
            WHERE role = 'INSPECTOR'
              AND menu_id IN :menu_ids
              AND deleted_at IS NULL
            """
        ).bindparams(sa.bindparam("menu_ids", expanding=True)),
        {"menu_ids": menu_ids},
    )
