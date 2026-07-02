"""menu: t_menu + t_role_menu + 默认菜单 seed

Revision ID: 000000000004
Revises: 000000000003
Create Date: 2026-07-02

要点：
- 新增 t_menu（菜单主表，邻接表）与 t_role_menu（角色↔菜单 N:M）。
- 与项目约定一致：DB 层无物理 FK，引用完整性由 service 层维护；
  parent_id 防单行自引用走 CheckConstraint `parent_id IS NULL OR parent_id <> id`。
- seed 在同一 upgrade 末尾插入默认菜单，1:1 镜像当前 MainLayout.vue
  硬编码侧边栏（首页 / 订单管理 / 权限管理 / 车间 四大块 + 子项）。
- 不复制 i18n 字段：title 直接存中文（与现有 t_shelf.name 同风格）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000000000004"
down_revision: Union[str, None] = "000000000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# seed 数据：先用 code 作 key，再映射成 snowflake id 以满足 parent_id
_MENU_SEED: list[dict] = [
    {"code": "home",            "parent": None,           "title": "首页",       "path": "/dashboard",      "icon": "House",     "sort_order": 10},
    {"code": "order_group",     "parent": None,           "title": "订单管理",   "path": None,              "icon": "Tickets",   "sort_order": 20},
    {"code": "parts_list",      "parent": "order_group",  "title": "零件一览",   "path": "/parts",          "icon": "Box",       "sort_order": 10},
    {"code": "parts_new",       "parent": "order_group",  "title": "新建零件",   "path": "/parts/new",      "icon": "Plus",      "sort_order": 20},
    {"code": "assemblies_list", "parent": "order_group",  "title": "装配件一览", "path": "/assemblies",     "icon": "Connection","sort_order": 30},
    {"code": "assemblies_new",  "parent": "order_group",  "title": "新建装配件", "path": "/assemblies/new", "icon": "Plus",      "sort_order": 40},
    {"code": "auth_group",      "parent": None,           "title": "权限管理",   "path": None,              "icon": "Key",       "sort_order": 30},
    {"code": "workers_list",    "parent": "auth_group",   "title": "工人一览",   "path": "/workers",        "icon": "User",      "sort_order": 10},
    {"code": "users_list",      "parent": "auth_group",   "title": "账号管理",   "path": "/users",          "icon": "List",      "sort_order": 20},
    {"code": "floor_group",     "parent": None,           "title": "车间",       "path": None,              "icon": "Tools",     "sort_order": 40},
    {"code": "shelves_list",    "parent": "floor_group",  "title": "货架管理",   "path": "/shelves",        "icon": "Platform",  "sort_order": 10},
    {"code": "scan_badge",      "parent": "floor_group",  "title": "扫码台",     "path": "/scan/badge",     "icon": "Promotion", "sort_order": 20},
]

_ROLE_MENU_SEED: list[tuple[str, str]] = [
    ("MANAGER", "home"),
    ("MANAGER", "order_group"),
    ("MANAGER", "parts_list"),
    ("MANAGER", "parts_new"),
    ("MANAGER", "assemblies_list"),
    ("MANAGER", "assemblies_new"),
    ("MANAGER", "auth_group"),
    ("MANAGER", "workers_list"),
    ("MANAGER", "users_list"),
    ("MANAGER", "floor_group"),
    ("MANAGER", "shelves_list"),
    ("MANAGER", "scan_badge"),
    ("SHELF_ACCOUNT", "home"),
    ("SHELF_ACCOUNT", "floor_group"),
    ("SHELF_ACCOUNT", "scan_badge"),
]


def upgrade() -> None:
    # 1) t_menu
    op.create_table(
        "t_menu",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=50), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=True),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # —— 审计字段 ——
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_t_menu_no_self_loop"),
    )
    # partial unique：`UniqueConstraint` 不支持 postgresql_where，用 op.create_index 替代。
    op.create_index(
        "uk_t_menu_code",
        "t_menu",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_menu_parent_id", "t_menu", ["parent_id"])
    op.create_index("ix_t_menu_deleted_at", "t_menu", ["deleted_at"])

    # 2) t_role_menu
    op.create_table(
        "t_role_menu",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("menu_id", sa.BigInteger(), nullable=False),
        # —— 审计字段 ——
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uk_t_role_menu_role_menu",
        "t_role_menu",
        ["role", "menu_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_role_menu_role", "t_role_menu", ["role"])
    op.create_index("ix_t_role_menu_menu_id", "t_role_menu", ["menu_id"])
    op.create_index("ix_t_role_menu_deleted_at", "t_role_menu", ["deleted_at"])

    # 3) seed 数据
    from utils.id_gen import new_id

    bind = op.get_bind()
    id_by_code: dict[str, int] = {}
    for row in _MENU_SEED:
        mid = new_id()
        id_by_code[row["code"]] = mid

    # 插入所有行；ON CONFLICT 处理"已经跑过 migration"的幂等场景。
    for row in _MENU_SEED:
        mid = id_by_code[row["code"]]
        parent_id = id_by_code.get(row["parent"]) if row["parent"] else None
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
                "id": mid,
                "parent_id": parent_id,
                "code": row["code"],
                "title": row["title"],
                "path": row["path"],
                "icon": row["icon"],
                "sort_order": row["sort_order"],
            },
        )

    # 回查实际写入的 id（处理"已存在"的 row，让后续 t_role_menu seed 拿到真 id）
    rows = bind.execute(
        sa.text("SELECT code, id FROM t_menu WHERE deleted_at IS NULL")
    ).fetchall()
    for code, mid in rows:
        id_by_code[code] = int(mid)

    # t_role_menu seed
    for role, code in _ROLE_MENU_SEED:
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


def downgrade() -> None:
    op.drop_index("ix_t_role_menu_deleted_at", table_name="t_role_menu")
    op.drop_index("ix_t_role_menu_menu_id", table_name="t_role_menu")
    op.drop_index("ix_t_role_menu_role", table_name="t_role_menu")
    op.drop_table("t_role_menu")

    op.drop_index("ix_t_menu_deleted_at", table_name="t_menu")
    op.drop_index("ix_t_menu_parent_id", table_name="t_menu")
    op.drop_table("t_menu")