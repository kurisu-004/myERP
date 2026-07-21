"""remove assemblies_new menu entry (route retired; legacy POST /assemblies data kept)

2026-07-21：「新建零件 / 新建装配件」合并到单一 /parts/new 页面（Tab 2 PDF 批量上传），
assemblies_new 菜单不再需要。本迁移：
- 软删 t_menu WHERE code='assemblies_new'（保留行便于审计回滚）
- 同步软删 MANAGER / CLERK 在 t_role_menu 中的对应行

为什么不直接 DELETE t_menu？
- t_menu.code 是 partial unique 索引 uk_t_menu_code 的过滤器；硬删会让
  downgrade 重新 INSERT 时违反唯一（即使 partial，新行的 deleted_at IS NULL
  也会与旧行冲突）。
- 软删 + role_menu 同步删除：恢复时再走 000000000002 的 _seed_menus 即可。

注意：本迁移不涉及任何 t_part / t_assembly 业务数据。
现有装配件的 detail 页面端点（upload-pdf / children / cancel / soft-delete）
保留以维护历史数据（service.AssemblyService 完整不动）。

Revision ID: 000000000008
Revises: 000000000007
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000008"
# 链在 prod_data/007 之后（与 006/007 同链）。
# 当前 prod_data 线性：001 → 002 → 003 → 005 → 006 → 007 → 008
down_revision: Union[str, None] = "000000000007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 本迁移涉及的角色列表（与 000000000002 中该菜单的角色绑定对齐：MANAGER + CLERK）
_AFFECTED_ROLES: tuple[str, ...] = ("MANAGER", "CLERK")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. 取出 assemblies_new 的 menu_id（仅未软删的活跃行）
    rows = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code = 'assemblies_new' AND deleted_at IS NULL"
        )
    ).fetchall()
    menu_ids = [int(r[0]) for r in rows]
    if not menu_ids:
        return

    # 2. 软删 t_menu（保留行；partial unique 索引仍允许新行 code 不同）
    bind.execute(
        sa.text(
            "UPDATE t_menu SET deleted_at = now(), updated_at = now() "
            "WHERE id IN :ids AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": menu_ids},
    )

    # 3. 同步软删 t_role_menu（让 MANAGER/CLERK 的 sidebar 立即消失；
    #    其他角色本来也没绑，无需清理）。
    bind.execute(
        sa.text(
            "UPDATE t_role_menu SET deleted_at = now(), updated_at = now() "
            "WHERE menu_id IN :ids AND role IN :roles AND deleted_at IS NULL"
        ).bindparams(
            sa.bindparam("ids", expanding=True),
            sa.bindparam("roles", expanding=True),
        ),
        {"ids": menu_ids, "roles": list(_AFFECTED_ROLES)},
    )


def downgrade() -> None:
    """回滚：恢复 assemblies_new 菜单 + MANAGER/CLERK 的 role_menu 行。

    实现策略：清掉旧软删行后重新 INSERT（用 ON CONFLICT 幂等），
    这样不管数据库处于「初次回滚」还是「再次回滚」都能成功。
    """
    from utils.id_gen import new_id

    bind = op.get_bind()

    # 1. 清掉旧的软删行（不论 t_menu 还是 t_role_menu）
    bind.execute(
        sa.text(
            "DELETE FROM t_role_menu "
            "WHERE menu_id IN (SELECT id FROM t_menu WHERE code = 'assemblies_new')"
        )
    )
    bind.execute(
        sa.text("DELETE FROM t_menu WHERE code = 'assemblies_new'")
    )

    # 2. 拿到 order_group 的 parent_id
    parent_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code = 'order_group' AND deleted_at IS NULL"
        )
    ).fetchone()
    if parent_row is None:
        return
    parent_id = int(parent_row[0])

    # 3. 重新 INSERT 菜单行
    new_menu_id = new_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_menu (id, parent_id, code, title, path, icon, sort_order,
                                is_active, created_at, updated_at)
            VALUES (:id, :parent_id, 'assemblies_new', '新建装配件',
                    '/assemblies/new', 'Plus', 40, true, now(), now())
            ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": new_menu_id, "parent_id": parent_id},
    )

    # 4. 拿回 menu_id（ON CONFLICT 时刚 INSERT 的 id 可能未生效，重新 SELECT）
    row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code = 'assemblies_new' AND deleted_at IS NULL"
        )
    ).fetchone()
    if row is None:
        return
    active_menu_id = int(row[0])

    # 5. 重新 INSERT role_menu 行（MANAGER + CLERK）
    for role in _AFFECTED_ROLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, :role, :menu_id, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "role": role, "menu_id": active_menu_id},
        )