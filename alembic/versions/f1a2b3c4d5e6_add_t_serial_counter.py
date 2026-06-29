"""add t_serial_counter (L/F/H seeded); null all t_part.serial_no

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-06-29

说明：
- 新增 t_serial_counter 表，PK 是 prefix（单字母 L/F/H），counter 为 BigInteger
  默认 0；counter 单调递增，序列号 = 1000 + counter % 5000。
- 种子数据：L / F / H 三行各 counter=0。
  ON CONFLICT DO NOTHING 让手动 rerun 也安全。
- 旧的 t_part.serial_no 全部置 NULL——按需求，序列号从新记录开始按
  新算法分配；旧 1000-9999 段作废。
- 不使用物理外键，与 CLAUDE.md 约定一致。
- 审计字段 (created_at / created_by / updated_at / updated_by / deleted_at)
  与项目其它表保持一致。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 建表
    op.create_table(
        "t_serial_counter",
        sa.Column("prefix", sa.String(length=1), primary_key=True),
        sa.Column(
            "counter",
            sa.BigInteger,
            nullable=False,
            server_default=sa.text("0"),
        ),
        # —— 审计字段（与 AuditMixin 对齐）——
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )

    # 2. 种子：L/F/H 三个 prefix 各一行。
    op.execute(
        sa.text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "VALUES ('L', 0), ('F', 0), ('H', 0) "
            "ON CONFLICT (prefix) DO NOTHING"
        )
    )

    # 3. 旧的 t_part.serial_no 全部置 NULL——按需求，序列号从新记录开始。
    # uk_t_part_serial_no 是部分唯一索引（WHERE serial_no IS NOT NULL），
    # NULL 不会触发冲突，所以这条 UPDATE 是安全的。
    op.execute(sa.text("UPDATE t_part SET serial_no = NULL"))


def downgrade() -> None:
    op.drop_table("t_serial_counter")
    # 不回填 t_part.serial_no：downgrade 之后若部署了旧版
    # find_next_serial_for_code，它会基于 NULL 重新算号（旧的 1000-9999 段）。
