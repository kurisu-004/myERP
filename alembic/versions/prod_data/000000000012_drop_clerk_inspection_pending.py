"""drop CLERK binding to inspection_pending menu

2026-07-23：CLERK 不再有品检权限（pass-inspection / fail-inspection）。

背景：
- `api/v1/part.py` 的 `_inspector_dep` 从
  ``(MANAGER, CLERK, INSPECTOR)`` 收紧为 ``(MANAGER, INSPECTOR)``。
- `alembic/versions/prod_data/000000000002_data_init.py::_seed_inspection_menu`
  同步从 ``("MANAGER", "CLERK", "INSPECTOR")`` 改为 ``("MANAGER", "INSPECTOR")``。
- 本迁移负责把已部署库中残留的 CLERK 行清掉（前端侧边栏 + 路由守卫
  均走 menu-code 成员判定，去掉这行后 CLERK 用户看不到「待品检」入口，
  直接访问 `/inspection/pending` 也会被路由守卫重定向）。

幂等 / 安全：
- 仅软删 (`deleted_at = now()`)，保留审计历史。
- 复用 `uk_t_role_menu_role_menu` 部分唯一索引（`WHERE deleted_at IS NULL`），
  后续可重复执行 / 反向操作（downgrade）不冲突。
- `inspection_pending` 菜单若不存在则 no-op。

Revision ID: 000000000012
Revises: 000000000011
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000012"
# 单 head 线性拓扑：… 010 → 011 → 012
down_revision: Union[str, None] = "000000000011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MENU_CODE = "inspection_pending"
_TARGET_ROLE = "CLERK"


def upgrade() -> None:
    bind = op.get_bind()
    # 找 inspection_pending 菜单 id；可能不存在（全新库 / 已删）。
    mid = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code=:c AND deleted_at IS NULL"
        ),
        {"c": _MENU_CODE},
    ).scalar()
    if mid is None:
        return

    # 软删 CLERK 行；保留历史供审计。
    bind.execute(
        sa.text(
            """
            UPDATE t_role_menu
               SET deleted_at = now(),
                   updated_at = now()
             WHERE role = :role
               AND menu_id = :mid
               AND deleted_at IS NULL
            """
        ),
        {"role": _TARGET_ROLE, "mid": int(mid)},
    )


def downgrade() -> None:
    """恢复 CLERK → inspection_pending 绑定。

    仅当被软删的行还存在时复活（保留 `created_at`）；不存在则重建。
    """
    bind = op.get_bind()
    from utils.id_gen import new_id

    mid = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code=:c AND deleted_at IS NULL"
        ),
        {"c": _MENU_CODE},
    ).scalar()
    if mid is None:
        return

    # 先恢复软删行（保留 created_at）。
    bind.execute(
        sa.text(
            """
            UPDATE t_role_menu
               SET deleted_at = NULL,
                   updated_at = now()
             WHERE role = :role
               AND menu_id = :mid
               AND deleted_at IS NOT NULL
            """
        ),
        {"role": _TARGET_ROLE, "mid": int(mid)},
    )

    # 若不存在任何 CLERK 行（含历史软删已被清掉的场景），补一行。
    existing = bind.execute(
        sa.text(
            """
            SELECT 1 FROM t_role_menu
             WHERE role = :role AND menu_id = :mid
            """
        ),
        {"role": _TARGET_ROLE, "mid": int(mid)},
    ).scalar()
    if existing:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
            VALUES (:id, :role, :menu_id, now(), now())
            """
        ),
        {"id": new_id(), "role": _TARGET_ROLE, "mid": int(mid)},
    )