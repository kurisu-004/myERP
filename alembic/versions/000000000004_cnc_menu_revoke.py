"""cnc_menu_revoke: 编程员入口合并到零件一览

Revision ID: 000000000004
Revises: 000000000003
Create Date: 2026-07-06

说明：
- 把 CNC_PROGRAMMER 角色从 `pending_programming` 菜单关联移除。
- 给 CNC_PROGRAMMER 加上 `parts_list` 菜单关联（让编程员进零件一览）。
- 编程员登录后由前端根据角色把 PartsList 默认状态过滤为 PROGRAMMING（见
  frontend/src/views/parts/PartsList.vue）。
- 既有 t_menu / t_user / t_user_role 全部不动；只追加 / 删除 t_role_menu 行。
- 升级：仅一次 SQL；downgrade 恢复。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "000000000004"
down_revision: Union[str, None] = "000000000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # 1) 移除 CNC_PROGRAMMER → pending_programming 的角色菜单关联。
    op.execute(
        """
        DELETE FROM t_role_menu
        WHERE role = 'CNC_PROGRAMMER'
          AND menu_id IN (
              SELECT id FROM t_menu
              WHERE code = 'pending_programming' AND deleted_at IS NULL
          )
        """
    )
    # 2) 给 CNC_PROGRAMMER 加 parts_list（若已存在则不重复）。
    op.execute(
        """
        INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
        SELECT
            (random() * 9000000000000000000 + 1000000000000000000)::bigint,
            'CNC_PROGRAMMER',
            m.id,
            now(),
            now()
        FROM t_menu m
        WHERE m.code = 'parts_list' AND m.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM t_role_menu rm
              WHERE rm.role = 'CNC_PROGRAMMER' AND rm.menu_id = m.id
                AND rm.deleted_at IS NULL
          )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    # 反向：把 parts_list 移除 + 恢复 pending_programming 关联。
    op.execute(
        """
        DELETE FROM t_role_menu
        WHERE role = 'CNC_PROGRAMMER'
          AND menu_id IN (
              SELECT id FROM t_menu
              WHERE code = 'parts_list' AND deleted_at IS NULL
          )
        """
    )
    op.execute(
        """
        INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
        SELECT
            (random() * 9000000000000000000 + 1000000000000000000)::bigint,
            'CNC_PROGRAMMER',
            m.id,
            now(),
            now()
        FROM t_menu m
        WHERE m.code = 'pending_programming' AND m.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM t_role_menu rm
              WHERE rm.role = 'CNC_PROGRAMMER' AND rm.menu_id = m.id
                AND rm.deleted_at IS NULL
          )
        """
    )
