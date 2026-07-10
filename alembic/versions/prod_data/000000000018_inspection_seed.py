"""inspection_seed: 品检打回端点配套数据 — inspection_pending 菜单 + INSPECTOR 角色用户

Revision ID: 000000000018
Revises: 000000000017
Create Date: 2026-07-10

要点（prod_data 类别 — dev + prod 都需要）：
- 状态机新增 fail_inspection (INSPECTION → ON_SHELF)，由 INSPECTOR 角色驱动。
- 新增 1 个菜单行 `inspection_pending`（独立叶子，挂 order_group），让
  INSPECTOR / CLERK / MANAGER 都能从侧边栏进入品检待办页。
- 新增 3 行 t_role_menu 把 inspection_pending 绑给三个角色。
- 新增 2 个品检员账号（黄道玉 / 曾学辉），对应 19 工人里工种=品检 的两个；
  username = phone（与 prod_seed 一致），password = changeme。
  t_user_role 行（role=INSPECTOR, scope_type=NULL, scope_id=NULL）。
- ON CONFLICT 全部幂等；与已有 dev_seed / prod_seed 用户、菜单共存。
- downgrade：删除本迁移写入的菜单 + 用户 + 角色关联。
"""
import bcrypt as _bc
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000018"
down_revision: Union[str, None] = "000000000017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 品检员账号（与 19 工人工种=品检 一一对应；username=phone）
_INSPECTOR_USERS: list[tuple[str, str]] = [
    ("18250705779", "黄道玉"),
    ("18046244109", "曾学辉"),
]


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()
    _seed_inspection_menu(bind)
    _seed_inspector_users(bind)


# =============================================================================
# 阶段 1：菜单 + 角色菜单关联
# =============================================================================
def _seed_inspection_menu(bind) -> None:
    """新增 `inspection_pending` 菜单 + MANAGER/CLERK/INSPECTOR 角色关联。

    挂到 order_group（与 parts_list 同级），sort_order 排在装配件之后。
    """
    # 1) 父菜单 order_group id
    parent_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='order_group' AND deleted_at IS NULL"
        )
    ).fetchone()
    if parent_row is None:
        return  # 父菜单都不存在；后续迁移不需要执行
    parent_id = int(parent_row[0])

    # 2) 菜单行（ON CONFLICT 后回查实际 id）
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
            "code": "inspection_pending",
            "title": "待品检",
            "path": "/inspection/pending",
            "icon": "CircleCheck",
            "sort_order": 50,
        },
    )

    mid_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='inspection_pending' AND deleted_at IS NULL"
        )
    ).fetchone()
    if mid_row is None:
        return
    mid = int(mid_row[0])

    # 3) 角色菜单关联（MANAGER / CLERK / INSPECTOR）
    for role in ("MANAGER", "CLERK", "INSPECTOR"):
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


# =============================================================================
# 阶段 2：品检员账号 + INSPECTOR 角色
# =============================================================================
def _seed_inspector_users(bind) -> None:
    """2 个品检员账号 + INSPECTOR 角色绑定。

    password = changeme（bcrypt rounds=12，与 prod_seed 一致）。
    username = phone，full_name = 中文姓名。
    """
    pwd_hash = _bc.hashpw(
        b"changeme", _bc.gensalt(rounds=12)
    ).decode("utf-8")

    for username, full_name in _INSPECTOR_USERS:
        # 1) t_user 行（若已存在则跳过）
        user_row = bind.execute(
            sa.text(
                "SELECT id FROM t_user "
                "WHERE username=:u AND deleted_at IS NULL"
            ),
            {"u": username},
        ).fetchone()

        if user_row is None:
            uid = new_id()
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_user
                      (id, username, password_hash, full_name, phone,
                       is_active, created_at, updated_at)
                    VALUES
                      (:id, :username, :pwd, :full_name, :phone,
                       true, now(), now())
                    ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                    """
                ),
                {
                    "id": uid,
                    "username": username,
                    "pwd": pwd_hash,
                    "full_name": full_name,
                    "phone": username,
                },
            )
            user_id = uid
        else:
            user_id = int(user_row[0])

        # 2) t_user_role 行（role=INSPECTOR, scope=NULL）
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role
                  (id, user_id, role, scope_type, scope_id,
                   created_at, updated_at)
                VALUES
                  (:id, :uid, 'INSPECTOR', NULL, NULL, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": user_id},
        )


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    bind = op.get_bind()

    # 1) 角色菜单关联（inspection_pending）
    op.execute(
        sa.text(
            "DELETE FROM t_role_menu WHERE role IN "
            "('MANAGER', 'CLERK', 'INSPECTOR') "
            "AND menu_id IN (SELECT id FROM t_menu "
            "                WHERE code='inspection_pending' "
            "                AND deleted_at IS NULL)"
        )
    )

    # 2) 菜单行
    op.execute(
        sa.text(
            "DELETE FROM t_menu WHERE code='inspection_pending' "
            "AND deleted_at IS NULL"
        )
    )

    # 3) 品检员角色 + 账号
    bind.execute(
        sa.text(
            "DELETE FROM t_user_role WHERE role='INSPECTOR' "
            "AND user_id IN (SELECT id FROM t_user "
            "                WHERE username IN :usernames "
            "                AND deleted_at IS NULL)"
        ).bindparams(sa.bindparam("usernames", expanding=True)),
        {"usernames": [u[0] for u in _INSPECTOR_USERS]},
    )

    bind.execute(
        sa.text(
            "DELETE FROM t_user WHERE username IN :usernames "
            "AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("usernames", expanding=True)),
        {"usernames": [u[0] for u in _INSPECTOR_USERS]},
    )