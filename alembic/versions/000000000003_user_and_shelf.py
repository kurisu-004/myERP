"""user_and_shelf: t_user + t_user_role + t_shelf; 去掉 READY; 重命名 current_worker_id

Revision ID: 000000000003
Revises: 000000000002
Create Date: 2026-07-01

要点：
- 新增 t_user / t_user_role / t_shelf 三张表；
- t_part.current_worker_id 重命名为 current_holder_id（语义拓宽为「多态 holder」）；
- t_part.released_at 列删除（READY 不再存在）；
- 新增 t_part.placed_at（PENDING→IN_PROCESS 时置位，供 Dashboard「已放置 X 分钟」）；
- 新增复合索引 `ix_t_part_status_holder (status, current_holder_id)`；
- 数据迁移：
    UPDATE t_part SET status = 'IN_PROCESS' WHERE status = 'READY' AND deleted_at IS NULL;
  （t_part_event 表的 RELEASED 老事件保留，仅 PartStatus.PLACED_ON_SHELF 是新代码触发的事件。）
- dev seed：受 `SHELF_SEED_ON_MIGRATE` 开关；开关为 true 时插入：
    - 1 条 MANAGER `admin/changeme`
    - 3 个货架：PROD-A1 / PROD-B1 / INSP-I1（各 is_active=true）
    - 3 条 SHELF_ACCOUNT 账号 `prodA1/prodB1/inspI1` 全 `changeme`，
      各 add_role(SHELF_ACCOUNT, scope=对应 shelf)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000000000003"
down_revision: Union[str, None] = "000000000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    seed_enabled = _should_seed()
    # 注：seed 在所有表建好后再跑（见函数末尾的 if 块）
    # =================================================================
    # 1) t_user：账号主表
    # =================================================================
    op.create_table(
        "t_user",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=50), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uk_t_user_username",
        "t_user",
        ["username"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_user_deleted_at", "t_user", ["deleted_at"])

    # =================================================================
    # 2) t_user_role：账号 ↔ 角色多对多
    #    scope_type='shelf' / scope_id=<shelf.id> 用于 SHELF_ACCOUNT。
    #    PKG 行为：(NULL, NULL) 不视为冲突；因此 MANAGER（无 scope）一条；SHELF_ACCOUNT 每 shelf 一条。
    # =================================================================
    op.create_table(
        "t_user_role",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_id", sa.BigInteger(), nullable=True),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "user_id", "role", "scope_type", "scope_id",
            name="uk_t_user_role_user_role_scope",
        ),
    )
    op.create_index(
        "ix_t_user_role_user_id", "t_user_role", ["user_id"],
    )
    op.create_index(
        "ix_t_user_role_scope", "t_user_role", ["scope_type", "scope_id"],
    )
    op.create_index("ix_t_user_role_deleted_at", "t_user_role", ["deleted_at"])

    # =================================================================
    # 3) t_shelf：货架实体（不带账号）
    # =================================================================
    op.create_table(
        "t_shelf",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("zone", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uk_t_shelf_code",
        "t_shelf",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_shelf_zone", "t_shelf", ["zone"])
    op.create_index("ix_t_shelf_deleted_at", "t_shelf", ["deleted_at"])

    # =================================================================
    # 4) t_part 列改造：
    #    - current_worker_id → current_holder_id（保留原索引，重命名）
    #    - released_at 删（READY 不再存在）
    #    - placed_at 新增
    #    - (status, current_holder_id) 复合索引新增（dashboard 按货架分组）
    # =================================================================
    op.alter_column(
        "t_part",
        "current_worker_id",
        new_column_name="current_holder_id",
    )
    # alembic op 没有 rename_index，用 raw SQL
    op.execute(sa.text("ALTER INDEX ix_t_part_current_worker_id RENAME TO ix_t_part_current_holder_id"))

    op.add_column(
        "t_part",
        sa.Column("placed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_part_placed_at", "t_part", ["placed_at"])
    op.create_index(
        "ix_t_part_status_holder",
        "t_part",
        ["status", "current_holder_id"],
    )

    op.drop_index("ix_t_part_released_at", table_name="t_part")
    op.drop_column("t_part", "released_at")

    # =================================================================
    # 5) 数据迁移：READY → IN_PROCESS
    #    `PartStatus.READY` 已从 Python enum 移除；这里把 DB 里的旧字符串也清掉。
    #    历史 RELEASED 事件（t_part_event.event_type='RELEASED'）保留不动。
    # =================================================================
    op.execute(
        sa.text(
            "UPDATE t_part SET status='IN_PROCESS' "
            "WHERE status='READY' AND deleted_at IS NULL"
        )
    )

    # =================================================================
    # 6) dev seed：受 env SHELF_SEED_ON_MIGRATE 控制
    #    true 时插入 1 个 MANAGER + 3 个货架 + 3 个 SHELF_ACCOUNT 账号 + 3 条 role 关联。
    #    密码统一 `changeme`（bcrypt 哈希见下面 _BCRYPT_HASH_CHANGEME，占位由 service 启动时检查）。
    # =================================================================
    bind = op.get_bind()
    seed_enabled = _should_seed()
    if seed_enabled:
        _seed_dev_data(bind)


def _should_seed() -> bool:  # pragma: no cover - dev-only toggle
    """读 SHELF_SEED_ON_MIGRATE 环境变量；aliembic 不依赖 .env，绕一圈。"""
    import os
    return os.environ.get("SHELF_SEED_ON_MIGRATE", "").lower() in (
        "1", "true", "yes",
    )


def _seed_dev_data(bind) -> None:  # pragma: no cover - dev seed
    """dev seed：1 MANAGER + 3 货架 + 3 SHELF_ACCOUNT 账号 + 3 角色关联。

    用 snowflake id 生成器（与生产代码保持一致）。
    bcrypt 哈希：直接用 `bcrypt` 库（避免 passlib 在新版 bcrypt 上的
    72-byte 检测探针失败）。
    """
    import bcrypt as _bc

    from utils.id_gen import new_id

    # 预先算好的哈希（rounds=4，dev only；首次启动时密码文字必须是 'changeme'）
    CHANGEME_HASH = _bc.hashpw(b"changeme", _bc.gensalt(rounds=4)).decode("utf-8")

    # 1) admin MANAGER
    admin_id = new_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user (id, username, password_hash, full_name, is_active, created_at, updated_at)
            VALUES (:id, 'admin', :pwd, '系统管理员', true, now(), now())
            ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": admin_id, "pwd": CHANGEME_HASH},
    )

    # 2) 3 个货架
    shelves = [
        ("PROD-A1", "生产区-A1 货架", "PRODUCTION"),
        ("PROD-B1", "生产区-B1 货架", "PRODUCTION"),
        ("INSP-I1", "品检区-1 货架", "INSPECTION"),
    ]
    shelf_ids: dict[str, int] = {}
    for code, name, zone in shelves:
        sid = new_id()
        bind.execute(
            sa.text(
                """
                INSERT INTO t_shelf (id, code, name, zone, is_active, created_at, updated_at)
                VALUES (:id, :code, :name, :zone, true, now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": sid, "code": code, "name": name, "zone": zone},
        )
        shelf_ids[code] = sid

    # 回查实际存在的 shelf.id（处理已存在的情况）
    rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_shelf WHERE deleted_at IS NULL AND code IN ('PROD-A1','PROD-B1','INSP-I1')"
        )
    ).fetchall()
    for code, sid in rows:
        shelf_ids[code] = int(sid)

    # 3) 3 个 SHELF_ACCOUNT 账号（username 一律小写，service 层做 case-insensitive 登录比对）
    shelf_users = [
        ("proda1", "proda1", "生产-A1 操作员", "PROD-A1"),
        ("prodb1", "prodb1", "生产-B1 操作员", "PROD-B1"),
        ("inspi1", "inspi1", "品检-1 操作员", "INSP-I1"),
    ]
    for _, username, full_name, _shelf_code in shelf_users:
        uid = new_id()
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user (id, username, password_hash, full_name, is_active, created_at, updated_at)
                VALUES (:id, :username, :pwd, :full_name, true, now(), now())
                ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": uid, "username": username, "pwd": CHANGEME_HASH, "full_name": full_name},
        )

    # 回查 user id
    user_id_map: dict[str, int] = {}
    rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user WHERE deleted_at IS NULL AND username IN ('proda1','prodb1','inspi1','admin')"
        )
    ).fetchall()
    for username, uid in rows:
        user_id_map[username] = int(uid)

    # 4) SHELF_ACCOUNT role 关联
    for _discard_code, username, _discard_name, shelf_code in shelf_users:
        uid = user_id_map.get(username)
        sid = shelf_ids.get(shelf_code)
        if uid is None or sid is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id, created_at, updated_at)
                VALUES (:id, :uid, 'SHELF_ACCOUNT', 'shelf', :sid, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": uid, "sid": sid},
        )

    # 5) admin MANAGER role
    admin_uid = user_id_map.get("admin")
    if admin_uid is not None:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id, created_at, updated_at)
                VALUES (:id, :uid, 'MANAGER', NULL, NULL, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": admin_uid},
        )


def downgrade() -> None:
    # 倒序回滚
    # 1) 恢复 t_part released_at + 列重命名
    op.add_column(
        "t_part",
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_part_released_at", "t_part", ["released_at"])

    op.drop_index("ix_t_part_status_holder", table_name="t_part")
    op.drop_index("ix_t_part_placed_at", table_name="t_part")
    op.drop_column("t_part", "placed_at")

    op.alter_column(
        "t_part",
        "current_holder_id",
        new_column_name="current_worker_id",
    )
    # alembic op 没有 rename_index，用 raw SQL
    op.execute(sa.text("ALTER INDEX ix_t_part_current_holder_id RENAME TO ix_t_part_current_worker_id"))

    # 2) 删新表
    op.drop_index("ix_t_shelf_deleted_at", table_name="t_shelf")
    op.drop_index("ix_t_shelf_zone", table_name="t_shelf")
    op.drop_index("uk_t_shelf_code", table_name="t_shelf")
    op.drop_table("t_shelf")

    op.drop_index("ix_t_user_role_deleted_at", table_name="t_user_role")
    op.drop_index("ix_t_user_role_scope", table_name="t_user_role")
    op.drop_index("ix_t_user_role_user_id", table_name="t_user_role")
    op.drop_table("t_user_role")

    op.drop_index("ix_t_user_deleted_at", table_name="t_user")
    op.drop_index("uk_t_user_username", table_name="t_user")
    op.drop_table("t_user")
