"""seed initial rbac data: superadmin + 7 builtin roles + sample menu permissions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---- pre-defined data ----
BUILTIN_ROLES = [
    ("SUPER_ADMIN", "超级管理员", 1, "绕过所有权限检查"),
    ("FACTORY_HEAD", "厂长", 1, "看所有数据"),
    ("MANAGER", "经理", 1, "订单全流程"),
    ("CLERK", "文员", 1, "录入订单/零件/工序"),
    ("WORKER", "工人", 1, "领取/报工"),
    ("QC", "品检", 1, "检验"),
    ("DRIVER", "司机", 1, "送货"),
]

# MENU + API 权限点 (按模块划分)
MENU_PERMS = [
    # (code, name, type, path, icon, component, sort_order)
    ("dashboard:menu", "首页", "MENU", "/dashboard", "House", "@/views/Dashboard.vue", 0),
    ("parts:menu", "基础数据", "MENU", None, "Document", None, 10),
    ("parts:list", "零件一览", "MENU", "/parts", "Box", "@/views/parts/PartsList.vue", 11),
    ("rbac:menu", "系统管理", "MENU", None, "Setting", None, 900),
    ("rbac:user:list", "用户管理", "MENU", "/rbac/users", "User", "@/views/rbac/UserList.vue", 901),
    ("rbac:role:list", "角色管理", "MENU", "/rbac/roles", "UserFilled", "@/views/rbac/RoleList.vue", 902),
    ("rbac:permission:list", "菜单权限", "MENU", "/rbac/permissions", "Key", "@/views/rbac/PermissionList.vue", 903),
]

# API 权限点
API_PERMS = [
    # rbac
    ("rbac:user:read", "查看用户", "API", "/api/v1/rbac/users", None, None, 0),
    ("rbac:user:write", "编辑用户", "API", "/api/v1/rbac/users", None, None, 0),
    ("rbac:role:read", "查看角色", "API", "/api/v1/rbac/roles", None, None, 0),
    ("rbac:role:write", "编辑角色", "API", "/api/v1/rbac/roles", None, None, 0),
    ("rbac:permission:read", "查看权限", "API", "/api/v1/rbac/permissions", None, None, 0),
    ("rbac:permission:write", "编辑权限", "API", "/api/v1/rbac/permissions", None, None, 0),
]


def _make_id_gen():
    """生成稳定的 id 序列 (基于 seed 序号) - 仅用于种子数据."""
    counter = [100_000]

    def _next():
        counter[0] += 1
        return counter[0]

    return _next


def upgrade() -> None:
    bind = op.get_bind()

    next_id = _make_id_gen()
    # 1) 插入 superadmin 用户 (id 用 next_id, 密码 admin123)
    import bcrypt
    pwd = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8")
    superadmin_id = next_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user (id, employee_no, username, password_hash, name, is_active, created_at, updated_at, deleted_at)
            VALUES (:id, 'SUPER001', 'superadmin', :pwd, '超级管理员', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
            ON CONFLICT (username) DO NOTHING
            """
        ),
        {"id": superadmin_id, "pwd": pwd},
    )
    # 拿回实际 superadmin id (可能是已存在的)
    row = bind.execute(
        sa.text("SELECT id FROM t_user WHERE username='superadmin'")
    ).first()
    superadmin_id = row[0]

    # 2) 插入 7 个内置角色
    role_id_map: dict[str, int] = {}
    for code, name, builtin, desc in BUILTIN_ROLES:
        rid = next_id()
        role_id_map[code] = rid
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role (id, code, name, description, is_builtin, created_at, updated_at, deleted_at)
                VALUES (:id, :code, :name, :desc, :builtin, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
                """
            ),
            {"id": rid, "code": code, "name": name, "desc": desc, "builtin": builtin},
        )

    # 3) 插入权限点
    perm_id_map: dict[str, int] = {}
    for code, name, type_, path, icon, component, sort_order in MENU_PERMS + API_PERMS:
        pid = next_id()
        perm_id_map[code] = pid
        bind.execute(
            sa.text(
                """
                INSERT INTO t_permission
                    (id, code, name, type, parent_id, path, icon, component, sort_order, is_enabled, created_at, updated_at, deleted_at)
                VALUES
                    (:id, :code, :name, :type, NULL, :path, :icon, :component, :sort, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
                """
            ),
            {
                "id": pid,
                "code": code,
                "name": name,
                "type": type_,
                "path": path,
                "icon": icon,
                "component": component,
                "sort": sort_order,
            },
        )

    # 4) 设置菜单的 parent_id
    bind.execute(
        sa.text(
            "UPDATE t_permission SET parent_id = :pid WHERE code IN ('parts:list')"
        ),
        {"pid": perm_id_map["parts:menu"]},
    )
    bind.execute(
        sa.text(
            "UPDATE t_permission SET parent_id = :pid WHERE code IN ('rbac:user:list','rbac:role:list','rbac:permission:list')"
        ),
        {"pid": perm_id_map["rbac:menu"]},
    )

    # 5) 角色绑权限 (去重)
    role_perm_bind = {
        "SUPER_ADMIN": list(perm_id_map.values()),
        "FACTORY_HEAD": [
            perm_id_map["dashboard:menu"],
            perm_id_map["parts:menu"],
            perm_id_map["parts:list"],
        ],
        "MANAGER": [
            perm_id_map["dashboard:menu"],
            perm_id_map["parts:menu"],
            perm_id_map["parts:list"],
        ],
        "CLERK": [
            perm_id_map["dashboard:menu"],
            perm_id_map["parts:menu"],
            perm_id_map["parts:list"],
        ],
        "WORKER": [
            perm_id_map["dashboard:menu"],
            perm_id_map["parts:menu"],
            perm_id_map["parts:list"],
        ],
        "QC": [
            perm_id_map["dashboard:menu"],
            perm_id_map["parts:menu"],
            perm_id_map["parts:list"],
        ],
        "DRIVER": [
            perm_id_map["dashboard:menu"],
        ],
    }
    for role_code, perm_ids in role_perm_bind.items():
        rid = role_id_map[role_code]
        for pid in set(perm_ids):
            link_id = next_id()
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_role_permission
                        (id, role_id, permission_id, created_at, updated_at, deleted_at)
                    VALUES (:id, :rid, :pid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
                    """
                ),
                {"id": link_id, "rid": rid, "pid": pid},
            )

    # 6) superadmin 绑 SUPER_ADMIN 角色
    link_id = next_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user_role
                (id, user_id, role_id, created_at, updated_at, deleted_at)
            VALUES (:id, :uid, :rid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
            """
        ),
        {"id": link_id, "uid": superadmin_id, "rid": role_id_map["SUPER_ADMIN"]},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM t_user_role WHERE user_id = 1"))
    bind.execute(sa.text("DELETE FROM t_role_permission"))
    bind.execute(sa.text("DELETE FROM t_user WHERE username = 'superadmin'"))
    bind.execute(sa.text("DELETE FROM t_permission"))
    bind.execute(sa.text("DELETE FROM t_role"))
