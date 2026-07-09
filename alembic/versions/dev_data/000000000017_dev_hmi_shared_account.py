"""dev_hmi_shared_account: 共享 HMI 账号 hmi-floor-1（SHELF_ACCOUNT scope=NULL）

Revision ID: 000000000017
Revises: 000000000016
Create Date: 2026-07-10

要点：
- 共享 HMI 场景（plan: glowing-giggling-liskov.md）：车间一台工控机被 N 个工人共用，
  系统不再「1 工人 = 1 SHELF_ACCOUNT 绑死单架」，而是「HMI 共享账号 = SHELF_ACCOUNT
  scope_id IS NULL（wildcard）」+ 工人身份靠 badge 扫码。
- dev seed 注入 `hmi-floor-1` 账号，username = password = `hmi-floor-1`（与
  其他 dev 账号 `changeme` 默认密码保持一致：bcrypt hash 一次写死）。
- role 行 `scope_type='shelf', scope_id=NULL` —— 这是关键，被
  `service/auth.py::_has_wildcard_shelf_account` 检测，触发 JWT
  `shelf_wildcard=True`，最终 `CurrentUser.can_operate_shelf` 对任意
  shelf_id 放行。
- 不动 prod seed（共享 HMI 是 dev 演示场景，prod 用户按需后台手动加）。
- ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING 兜底幂等。
- downgrade 不删 dev 数据（dev_seed 风格保持一致）。
"""
from typing import Sequence, Union

import bcrypt
from alembic import op
import sqlalchemy as sa

from utils.id_gen import new_id


revision: str = "000000000017"
down_revision: Union[str, None] = "000000000016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _hash_pwd(plain: str) -> str:
    # 与 core.security.hash_password 一致：bcrypt rounds=12
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def upgrade() -> None:
    bind = op.get_bind()
    pwd = _hash_pwd("changeme")  # dev 默认密码

    # 1) hmi-floor-1 账号
    hmi_user_id = new_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user (id, username, password_hash, full_name, is_active, created_at, updated_at)
            VALUES (:id, 'hmi-floor-1', :pwd, '车间共享 HMI', true, now(), now())
            ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": hmi_user_id, "pwd": pwd},
    )
    # 回查实际 id（ON CONFLICT 时不写新行；这里用 username 查实际 id 兜底）
    actual_uid = bind.execute(
        sa.text("SELECT id FROM t_user WHERE username = 'hmi-floor-1' AND deleted_at IS NULL")
    ).scalar()
    if actual_uid is None:
        return

    # 2) SHELF_ACCOUNT role 行（scope_id IS NULL = wildcard）
    role_id = new_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id, created_at, updated_at)
            VALUES (:id, :uid, 'SHELF_ACCOUNT', 'shelf', NULL, now(), now())
            ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
            """
        ),
        {"id": role_id, "uid": actual_uid},
    )


def downgrade() -> None:
    # dev 共享账号随 dev_seed 走；down 时不删（与 dev_seed 风格一致）
    pass
