"""seed 曾文洪经理账号 + C2 货架 + hmi-a1/hmi-b1 货架重绑 + 镀膜长改名

2026-07-26 一次性运维变更：

1. INSERT t_user (username=13606071983, full_name=曾文洪, role=MANAGER)。
2. INSERT t_shelf (code=C2, zone=PRODUCTION, name='生产/外协 C2')；自动按
   5 个 INHOUSE 工序灌 t_shelf_process 映射（参照
   000000000002::_seed_shelf_process_map 模式）。
3. 给 hmi-a1 / hmi-b1 补 SHELF_ACCOUNT 绑定 (C1, C2)：
   - hmi-a1 旧绑 A1, A2 → 本次新增 C1, C2
   - hmi-b1 旧绑 B1     → 本次新增 C1, C2
4. UPDATE t_customer：镀膜长 → 镀膜厂（修正 000000000002 第 151 行带 TODO
   注释的疑似笔误；该行原文 "按用户原话录入（疑为「镀膜厂」之误，但需用户
   确认）"，本次用户确认并纠正）。

幂等：
- 所有 INSERT 走 ON CONFLICT ... DO NOTHING（partial unique `WHERE deleted_at IS NULL`）。
- customer 改名走"目标名已存在则跳过"前置查询模式（无 UNIQUE 约束）。
- 重复跑无副作用。

Revision ID: 000000000016
Revises: 000000000015
Create Date: 2026-07-26
"""
from typing import Sequence, Union

import bcrypt as _bc
from alembic import op
import sqlalchemy as sa


revision: str = "000000000016"
# 链在 schema/015 之后，保持 CLAUDE.md §alembic 承诺的单 head 线性拓扑
down_revision: Union[str, None] = "000000000015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# 常量
# =============================================================================
# (username, full_name) —— 与既有 000000000002 密码策略一致：changeme (rounds=12)
_NEW_USER: tuple[str, str] = ("13606071983", "曾文洪")

# (code, name) —— C2 是生产/外协专用，落在 PRODUCTION 区（ShelfZone 枚举当前
# 只有 PRODUCTION / INHOUSE 两个值，无 OUTSOURCE；schema 字段无 CHECK 约束，
# service 层枚举校验）。
_NEW_SHELF: tuple[str, str] = ("C2", "生产/外协 C2")

# 本次**新增**的 (hmi_username, [shelf_code, ...]) 配对。旧绑定
# (hmi-a1→A1,A2 / hmi-b1→B1) 由 000000000002 创建并保留，本迁移不动。
_HMI_NEW_BINDINGS: dict[str, list[str]] = {
    "hmi-a1": ["C1", "C2"],
    "hmi-b1": ["C1", "C2"],
}

# (old_name, new_name) —— 客户改名
_CUSTOMER_RENAME: tuple[str, str] = ("镀膜长", "镀膜厂")


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()
    from utils.id_gen import new_id
    _seed_manager_user(bind, new_id)
    _seed_c2_shelf(bind, new_id)
    _seed_shelf_process_for_c2(bind, new_id)
    _extend_hmi_bindings(bind, new_id)
    _rename_customer(bind)


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    bind = op.get_bind()
    _unrename_customer(bind)
    _shrink_hmi_bindings(bind)
    _delete_c2_shelf_process(bind)
    _delete_c2_shelf(bind)
    _delete_manager_user(bind)


# =============================================================================
# upgrade helpers
# =============================================================================
def _seed_manager_user(bind, new_id) -> None:
    """新增经理账号 曾文洪（13606071983 / changeme）。"""
    pwd_hash = _bc.hashpw(b"changeme", _bc.gensalt(rounds=12)).decode("utf-8")
    username, full_name = _NEW_USER

    # 1a. INSERT t_user（partial unique `WHERE deleted_at IS NULL`）
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user (id, username, password_hash, full_name, phone,
                                is_active, created_at, updated_at)
            VALUES (:id, :username, :pwd, :full_name, :phone,
                    true, now(), now())
            ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {
            "id": new_id(),
            "username": username,
            "pwd": pwd_hash,
            "full_name": full_name,
            "phone": username,
        },
    )

    # 1b. 查 user id → INSERT MANAGER 角色（4 元唯一索引）
    uid = bind.execute(
        sa.text("SELECT id FROM t_user WHERE username=:u AND deleted_at IS NULL"),
        {"u": username},
    ).scalar()
    if uid is None:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id,
                                     created_at, updated_at)
            VALUES (:id, :uid, 'MANAGER', NULL, NULL, now(), now())
            ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
            """
        ),
        {"id": new_id(), "uid": int(uid)},
    )


def _seed_c2_shelf(bind, new_id) -> None:
    """新增生产/外协专用货架 C2（PRODUCTION 区）。"""
    code, name = _NEW_SHELF
    bind.execute(
        sa.text(
            """
            INSERT INTO t_shelf (id, code, name, zone, location, is_active,
                                 display_order, version, created_at, updated_at)
            VALUES (:id, :code, :name, 'PRODUCTION', NULL, true, 0, 0,
                    now(), now())
            ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": new_id(), "code": code, "name": name},
    )


def _seed_shelf_process_for_c2(bind, new_id) -> None:
    """给 C2 自动灌 5 个 INHOUSE 工序映射（车/铣/磨/CNC/线切割）。

    参照 000000000002::_seed_shelf_process_map 模式，但只针对 C2 一个架。
    若 C2 尚未存在（极端情况）则 no-op。
    """
    shelf_id = bind.execute(
        sa.text("SELECT id FROM t_shelf WHERE code=:c AND deleted_at IS NULL"),
        {"c": _NEW_SHELF[0]},
    ).scalar()
    if shelf_id is None:
        return

    proc_rows = bind.execute(
        sa.text(
            "SELECT id, sort_order FROM t_process "
            "WHERE deleted_at IS NULL AND category = 'INHOUSE' "
            "ORDER BY sort_order ASC"
        )
    ).fetchall()
    if not proc_rows:
        return

    for idx, (pid, _sort_order) in enumerate(proc_rows):
        bind.execute(
            sa.text(
                """
                INSERT INTO t_shelf_process
                  (id, shelf_id, process_id, sort_order,
                   created_at, updated_at, version, created_by, updated_by)
                VALUES
                  (:id, :sid, :pid, :so, now(), now(), 0, NULL, NULL)
                ON CONFLICT (shelf_id, process_id)
                  WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "sid": int(shelf_id), "pid": int(pid), "so": idx},
        )


def _extend_hmi_bindings(bind, new_id) -> None:
    """给 hmi-a1 / hmi-b1 补 (C1, C2) SHELF_ACCOUNT 绑定。

    旧绑定（hmi-a1→A1,A2；hmi-b1→B1）由 000000000002 创建并保留；本函数只
    INSERT 新增的 (user, shelf) 配对，ON CONFLICT 保证重复跑幂等。
    """
    new_codes = sorted({c for codes in _HMI_NEW_BINDINGS.values() for c in codes})

    user_rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user "
            "WHERE deleted_at IS NULL AND username IN :usernames"
        ).bindparams(sa.bindparam("usernames", expanding=True)),
        {"usernames": list(_HMI_NEW_BINDINGS.keys())},
    ).fetchall()
    user_id_by_name: dict[str, int] = {u: int(uid) for u, uid in user_rows}

    shelf_rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_shelf "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": new_codes},
    ).fetchall()
    shelf_id_by_code: dict[str, int] = {c: int(sid) for c, sid in shelf_rows}

    for hmi_username, codes in _HMI_NEW_BINDINGS.items():
        uid = user_id_by_name.get(hmi_username)
        if uid is None:
            continue
        for code in codes:
            sid = shelf_id_by_code.get(code)
            if sid is None:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_user_role
                      (id, user_id, role, scope_type, scope_id,
                       created_at, updated_at)
                    VALUES
                      (:id, :uid, 'SHELF_ACCOUNT', 'shelf', :sid, now(), now())
                    ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                    """
                ),
                {"id": new_id(), "uid": uid, "sid": sid},
            )


def _rename_customer(bind) -> None:
    """镀膜长 → 镀膜厂（修正 000000000002 第 151 行的疑似笔误）。

    t_customer.name 无 UNIQUE 约束，先查目标名是否已存在；存在则视为已完成。
    """
    old_name, new_name = _CUSTOMER_RENAME
    exists = bind.execute(
        sa.text("SELECT 1 FROM t_customer WHERE name=:n AND deleted_at IS NULL"),
        {"n": new_name},
    ).scalar()
    if exists:
        return
    bind.execute(
        sa.text(
            """
            UPDATE t_customer
            SET name = :new_name, updated_at = now()
            WHERE name = :old_name AND deleted_at IS NULL
            """
        ),
        {"new_name": new_name, "old_name": old_name},
    )


# =============================================================================
# downgrade helpers
# =============================================================================
def _unrename_customer(bind) -> None:
    """镀膜厂 → 镀膜长（恢复 000000000002 原状；目标名已存在则 no-op）。"""
    old_name, new_name = _CUSTOMER_RENAME
    exists = bind.execute(
        sa.text("SELECT 1 FROM t_customer WHERE name=:n AND deleted_at IS NULL"),
        {"n": old_name},
    ).scalar()
    if exists:
        return
    bind.execute(
        sa.text(
            """
            UPDATE t_customer
            SET name = :old_name, updated_at = now()
            WHERE name = :new_name AND deleted_at IS NULL
            """
        ),
        {"new_name": new_name, "old_name": old_name},
    )


def _shrink_hmi_bindings(bind) -> None:
    """清掉本迁移新增的 hmi-a1/b1 → {C1, C2} SHELF_ACCOUNT 绑定。

    不动 hmi-a1 的 (A1, A2) / hmi-b1 的 (B1) 旧绑定（属于 000000000002）。
    """
    new_codes = sorted({c for codes in _HMI_NEW_BINDINGS.values() for c in codes})
    bind.execute(
        sa.text(
            """
            DELETE FROM t_user_role
            WHERE role = 'SHELF_ACCOUNT'
              AND scope_type = 'shelf'
              AND scope_id IN (SELECT id FROM t_shelf
                               WHERE code IN :codes
                                 AND deleted_at IS NULL)
              AND user_id IN (SELECT id FROM t_user
                              WHERE username IN :usernames
                                AND deleted_at IS NULL)
            """
        ).bindparams(
            sa.bindparam("codes", expanding=True),
            sa.bindparam("usernames", expanding=True),
        ),
        {
            "codes": new_codes,
            "usernames": list(_HMI_NEW_BINDINGS.keys()),
        },
    )


def _delete_c2_shelf_process(bind) -> None:
    """清掉 C2 的所有 t_shelf_process 映射（5 个 INHOUSE 工序）。

    C1 自身由 000000000002 创建且 zone=INSPECTION，不会有 shelf_process 行
    （data_init::_seed_shelf_process_map 只灌 PRODUCTION 货架），所以无需清理。
    """
    bind.execute(
        sa.text(
            """
            DELETE FROM t_shelf_process
            WHERE shelf_id = (SELECT id FROM t_shelf
                              WHERE code = :c AND deleted_at IS NULL)
            """
        ),
        {"c": _NEW_SHELF[0]},
    )


def _delete_c2_shelf(bind) -> None:
    """删 C2 货架。"""
    bind.execute(
        sa.text("DELETE FROM t_shelf WHERE code = :c AND deleted_at IS NULL"),
        {"c": _NEW_SHELF[0]},
    )


def _delete_manager_user(bind) -> None:
    """删曾文洪的 MANAGER 角色绑定 + t_user 行。"""
    bind.execute(
        sa.text(
            """
            DELETE FROM t_user_role
            WHERE role = 'MANAGER'
              AND user_id = (SELECT id FROM t_user
                             WHERE username = :u AND deleted_at IS NULL)
            """
        ),
        {"u": _NEW_USER[0]},
    )
    bind.execute(
        sa.text("DELETE FROM t_user WHERE username = :u AND deleted_at IS NULL"),
        {"u": _NEW_USER[0]},
    )