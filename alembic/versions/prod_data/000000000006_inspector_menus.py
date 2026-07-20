"""grant INSPECTOR menu access for parts/assemblies/outsource send-receive

PR-I（2026-07-20）：扩大 INSPECTOR（品检员）菜单权限，让品检员可浏览：

- 零件一览（parts_list）—— 只读
- 装配件一览（assemblies_list）—— 只读
- 外协发送/接收（outsource_send_receive_list）—— INSPECTOR 扫码批量发送/接收外协件

菜单代码均已存在于 t_menu（000000000002 seed），本迁移只补 role_menu 行。
幂等：ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING。

注意：
- inspection_pending 已在 000000000002 的 _seed_inspection_menu 绑过 INSPECTOR（line 796），无需重复。
- API 端的 permissions 在 api/v1/{part,assembly,outsource_quote,outsource_company}.py 同步调整。

Revision ID: 000000000006
Revises: 000000000005
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000006"
# 链在 schema/005 之后，保持 CLAUDE.md §alembic 承诺的单 head 线性拓扑
# 001 → 002 → 003 → 005 → 006
down_revision: Union[str, None] = "000000000005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 新增 INSPECTOR 可见的菜单 code 列表（菜单已存在，只需补 t_role_menu）。
_INSPECTOR_MENUS: list[str] = [
    "parts_list",
    "assemblies_list",
    "outsource_send_receive_list",
]


def upgrade() -> None:
    bind = op.get_bind()

    # 取出要授权的 menu_id（已存在于 000000000002 的 _MENUS）
    menu_rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": _INSPECTOR_MENUS},
    ).fetchall()
    id_by_code = {code: int(mid) for code, mid in menu_rows}

    # 与 000000000002 的 _seed_menus 同款：snowflake id + ON CONFLICT 幂等
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
    # 回滚：移除 INSPECTOR 在本迁移新增的 3 行 t_role_menu。
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