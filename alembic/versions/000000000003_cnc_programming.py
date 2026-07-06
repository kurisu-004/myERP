"""cnc_programming: 新增 CNC 编程环节

Revision ID: 000000000003
Revises: 000000000002
Create Date: 2026-07-06

说明：
- 新增 t_cnc_program 表（CNC 程序 / G 代码文件元数据）
  * `part_id` 普通列 + 普通索引（DB 层无物理外键，遵守项目约定 1）。
  * `file_type` 用大写扩展名（NC / TAP / CNC / MPF / NGC），与 t_drawing_file 一致。
- 装配/接线 CLERK + CNC_PROGRAMMER 角色所需的菜单 + 角色关联。
  * 1 admin MANAGER + 3 SHELF_ACCOUNT 账号（已在 000000000002 写入，本迁移不动）。
  * 新增 1 CLERK 账号 + 1 CNC_PROGRAMMER 账号（口令同 `changeme`，dev only）。
- 不修改 t_part / t_user_role / t_role_menu 既有行；只追加。
"""
import random
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000003"
down_revision: Union[str, None] = "000000000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()

    # -------------------------------------------------------------------------
    # 1. t_cnc_program DDL
    # -------------------------------------------------------------------------
    op.create_table(
        "t_cnc_program",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("part_id", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False,
                  comment="G 代码扩展名大写（NC / TAP / CNC / MPF / NGC）"),
        sa.Column("object_key", sa.String(length=500), nullable=False,
                  comment="COS 对象 key"),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("upload_status", sa.String(length=20), nullable=False,
                  server_default="READY",
                  comment="PENDING / READY / FAILED"),
        # AuditMixin
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_cnc_program_part", "t_cnc_program", ["part_id"])
    op.create_index("ix_t_cnc_program_part_type", "t_cnc_program", ["part_id", "file_type"])
    op.create_index("ix_t_cnc_program_created_at", "t_cnc_program", ["created_at"])

    # -------------------------------------------------------------------------
    # 2. 菜单 / 角色菜单：待编程一览 + CLERK / CNC_PROGRAMMER 可见的菜单
    # -------------------------------------------------------------------------
    _seed_cnc_menus_and_role_assignments()


def _seed_cnc_menus_and_role_assignments() -> None:
    """插入 CNC 编程相关菜单 + CLERK / CNC_PROGRAMMER 角色关联。"""
    bind = op.get_bind()

    # 拿到既有 order_group 菜单 id（CLERK 的「零件一览/新建零件/装配件」都挂其下）
    rows = bind.execute(
        sa.text("SELECT code, id FROM t_menu WHERE deleted_at IS NULL")
    ).fetchall()
    id_by_code: dict[str, int] = {code: int(mid) for code, mid in rows}

    # 1) 待编程一览菜单
    pending_id = id_by_code.get("pending_programming")
    if pending_id is None:
        pending_id = new_id()
        bind.execute(
            sa.text(
                """
                INSERT INTO t_menu
                  (id, parent_id, code, title, path, icon, sort_order, is_active,
                   created_at, updated_at)
                VALUES
                  (:id, NULL, 'pending_programming', '待编程一览', '/cnc/pending',
                   'Cpu', 25, true, now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": pending_id},
        )
        # ON CONFLICT 时回查已有 id
        row = bind.execute(
            sa.text(
                "SELECT id FROM t_menu "
                "WHERE code='pending_programming' AND deleted_at IS NULL"
            )
        ).fetchone()
        if row:
            pending_id = int(row[0])
    id_by_code["pending_programming"] = pending_id

    # 2) 角色菜单关联：CNC_PROGRAMMER / CLERK 各自的可见范围
    role_menu_seed: list[tuple[str, str]] = [
        # CNC 编程员：首页 + 待编程一览 + 车间（扫码台）+ 详情所需菜单
        ("CNC_PROGRAMMER", "home"),
        ("CNC_PROGRAMMER", "pending_programming"),
        ("CNC_PROGRAMMER", "floor_group"),
        ("CNC_PROGRAMMER", "scan_badge"),
        # CLERK 文员：首页 + 订单管理（含零件 / 装配件 / 新建） + 详情子菜单
        ("CLERK", "home"),
        ("CLERK", "order_group"),
        ("CLERK", "parts_list"),
        ("CLERK", "parts_new"),
        ("CLERK", "assemblies_list"),
        ("CLERK", "assemblies_new"),
    ]
    for role, code in role_menu_seed:
        mid = id_by_code.get(code)
        if mid is None:
            continue
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

    # -------------------------------------------------------------------------
    # 3. seed 账号：1 CLERK + 1 CNC_PROGRAMMER
    # -------------------------------------------------------------------------
    import bcrypt as _bc

    changeme_hash = _bc.hashpw(b"changeme", _bc.gensalt(rounds=4)).decode("utf-8")

    seed_users = [
        ("clerk", "clerk", "前台文员", "CLERK"),
        ("cncprog", "cncprog", "CNC 编程员", "CNC_PROGRAMMER"),
    ]
    for username, uname_value, full_name, role in seed_users:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user (id, username, password_hash, full_name, is_active, created_at, updated_at)
                VALUES (:id, :username, :pwd, :full_name, true, now(), now())
                ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "username": uname_value, "pwd": changeme_hash,
             "full_name": full_name},
        )

    # 关联 user_role（无 scope）
    rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user "
            "WHERE deleted_at IS NULL AND username IN ('clerk','cncprog')"
        )
    ).fetchall()
    user_id_map: dict[str, int] = {username: int(uid) for username, uid in rows}

    for _username, uname_value, _full_name, role in seed_users:
        uid = user_id_map.get(uname_value)
        if uid is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id, created_at, updated_at)
                VALUES (:id, :uid, :role, NULL, NULL, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": uid, "role": role},
        )


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DELETE FROM t_role_menu WHERE role IN ('CLERK', 'CNC_PROGRAMMER')")
    op.execute(
        "DELETE FROM t_user_role WHERE role IN ('CLERK', 'CNC_PROGRAMMER')"
    )
    op.execute(
        "DELETE FROM t_user WHERE username IN ('clerk', 'cncprog') "
        "AND deleted_at IS NULL"
    )
    op.execute(
        "DELETE FROM t_menu WHERE code = 'pending_programming' "
        "AND deleted_at IS NULL"
    )
    op.drop_index("ix_t_cnc_program_part_type", table_name="t_cnc_program")
    op.drop_index("ix_t_cnc_program_part", table_name="t_cnc_program")
    op.drop_index("ix_t_cnc_program_created_at", table_name="t_cnc_program")
    op.drop_table("t_cnc_program")
