"""add_user_refresh_token_version: t_user 加 refresh_token_version 列（轮转计数器）

Revision ID: 000000000020
Revises: 000000000019
Create Date: 2026-07-10

要点：
- 2026-07-10 JWT 双 token 自动刷新上线（plan 文件
  jwt-token-token-token-robust-sonnet.md）：refresh token 启用轮转，
  每次 POST /auth/refresh 成功 → 该用户的 refresh_token_version 自增 1，
  旧 refresh token（payload.ver 落后）立即失效。
- 新增 `refresh_token_version INTEGER NOT NULL DEFAULT 0` 列；
  老用户的"未刷新过"状态用 0 表示，无需 backfill。
- 不加索引：refresh 走 PK 取行（users.get_by_id），version 是单行 +1，
  无查询场景需要索引。
- 不引入物理外键（与 CLAUDE.md §1 一致）。
- downgrade：drop column；老 refresh token 因此也会失效（线上不留孤儿）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000000000020"
down_revision: Union[str, None] = "000000000019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_user",
        sa.Column(
            "refresh_token_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="refresh token 轮转计数器；每次成功 refresh 后 +1",
        ),
    )


def downgrade() -> None:
    op.drop_column("t_user", "refresh_token_version")
