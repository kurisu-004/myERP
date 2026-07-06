"""customer_management: 新增客户管理一级菜单 + 申请人表 + 客户/申请人 CRUD 端点

Revision ID: 000000000005
Revises: 000000000004
Create Date: 2026-07-06

说明：
- 新增 t_applicant 表（申请人：姓名 + 所属一级客户，多对一）。
  * `customer_id` 普通列 + 普通索引（DB 层无物理外键，遵守项目约定 1）。
  * `name` 50 字符内，与 customer_id 联合唯一（同一一级客户下不允许重名，
    跨客户允许；软删后允许重建）—— 用 partial unique index 实现。
- 新增 1 个一级分组菜单 + 2 个二级叶子菜单（客户一览 / 申请人一览）。
- MANAGER + CLERK 两个角色对 3 个 code 全部可见。
- 不动既有 t_customer / t_menu / t_role_menu 行；只追加。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000005"
down_revision: Union[str, None] = "000000000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()

    # -------------------------------------------------------------------------
    # 1. t_applicant DDL
    # -------------------------------------------------------------------------
    op.create_table(
        "t_applicant",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False,
                  comment="申请人姓名"),
        sa.Column("customer_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_customer.id（一级客户）"),
        # AuditMixin
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_applicant_customer_id", "t_applicant", ["customer_id"])
    op.create_index("ix_t_applicant_name", "t_applicant", ["name"])
    op.create_index("ix_t_applicant_deleted_at", "t_applicant", ["deleted_at"])
    # 部分唯一索引：同一一级客户下不允许重名；软删后允许重建。
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_t_applicant_name_customer_active
            ON t_applicant (name, customer_id)
            WHERE deleted_at IS NULL
            """
        )
    )

    # -------------------------------------------------------------------------
    # 2. 菜单 / 角色菜单：客户管理 + 客户一览 + 申请人一览
    # -------------------------------------------------------------------------
    _seed_customer_management_menus()


def _seed_customer_management_menus() -> None:
    """插入客户管理一级分组 + 2 个二级叶子菜单 + MANAGER/CLERK 角色关联。"""
    bind = op.get_bind()

    rows = bind.execute(
        sa.text("SELECT code, id FROM t_menu WHERE deleted_at IS NULL")
    ).fetchall()
    id_by_code: dict[str, int] = {code: int(mid) for code, mid in rows}

    # 1) 三个菜单行（按 sort_order 排）
    menu_seed: list[dict] = [
        # 一级分组（path=NULL → 渲染为 el-sub-menu）
        {
            "code": "customer_management",
            "parent": None,
            "title": "客户管理",
            "path": None,
            "icon": "OfficeBuilding",
            "sort_order": 15,
        },
        # 二级叶子：客户一览
        {
            "code": "customers_list",
            "parent": "customer_management",
            "title": "客户一览",
            "path": "/customers",
            "icon": "Connection",
            "sort_order": 10,
        },
        # 二级叶子：申请人一览
        {
            "code": "applicants_list",
            "parent": "customer_management",
            "title": "申请人一览",
            "path": "/applicants",
            "icon": "User",
            "sort_order": 20,
        },
    ]
    for row in menu_seed:
        if id_by_code.get(row["code"]) is not None:
            continue
        new_menu_id = new_id()
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
                "id": new_menu_id,
                "parent_id": parent_id,
                "code": row["code"],
                "title": row["title"],
                "path": row["path"],
                "icon": row["icon"],
                "sort_order": row["sort_order"],
            },
        )
        # ON CONFLICT 时回查已有 id
        row_db = bind.execute(
            sa.text(
                "SELECT id FROM t_menu "
                "WHERE code=:code AND deleted_at IS NULL"
            ),
            {"code": row["code"]},
        ).fetchone()
        if row_db:
            id_by_code[row["code"]] = int(row_db[0])

    # 2) 角色菜单关联：MANAGER + CLERK 都对这 3 个 code 可见
    role_menu_seed: list[tuple[str, str]] = [
        ("MANAGER", "customer_management"),
        ("MANAGER", "customers_list"),
        ("MANAGER", "applicants_list"),
        ("CLERK", "customer_management"),
        ("CLERK", "customers_list"),
        ("CLERK", "applicants_list"),
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


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    bind = op.get_bind()

    # 1) 角色菜单关联
    bind.execute(
        sa.text(
            "DELETE FROM t_role_menu WHERE role IN ('MANAGER', 'CLERK') "
            "AND menu_id IN (SELECT id FROM t_menu "
            "                WHERE code IN ('customer_management', "
            "                               'customers_list', "
            "                               'applicants_list') "
            "                AND deleted_at IS NULL)"
        )
    )
    # 2) 菜单行
    bind.execute(
        sa.text(
            "DELETE FROM t_menu WHERE code IN ('customer_management', "
            "                                    'customers_list', "
            "                                    'applicants_list') "
            "AND deleted_at IS NULL"
        )
    )
    # 3) t_applicant
    op.execute("DROP INDEX IF EXISTS uq_t_applicant_name_customer_active")
    op.drop_index("ix_t_applicant_deleted_at", table_name="t_applicant")
    op.drop_index("ix_t_applicant_name", table_name="t_applicant")
    op.drop_index("ix_t_applicant_customer_id", table_name="t_applicant")
    op.drop_table("t_applicant")