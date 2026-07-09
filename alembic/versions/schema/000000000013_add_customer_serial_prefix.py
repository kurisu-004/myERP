"""add_customer_serial_prefix: t_customer 加 serial_prefix 列（A-Z 单字符）

Revision ID: 000000000013
Revises: 000000000012
Create Date: 2026-07-09

要点（不引入物理外键）：
- t_customer.serial_prefix String(1) nullable，对一级客户必填（service 层校验），
  叶子客户 NULL（继承父）。
- 现有 3 个一级客户（法拉电子 / 路达 / 宏发）按 PARENT_TO_CODE 兜底回填
  F / L / H —— 把历史硬编码映射迁移到 DB 列。
- check constraint 限定大写字母：`serial_prefix IS NULL OR serial_prefix ~ '^[A-Z]$'`。
- 部分唯一索引 `uq_t_customer_root_prefix` 仅约束未软删的根客户（parent_id IS NULL
  AND serial_prefix IS NOT NULL），与 t_applicant 的 partial unique 同款。
- down_revision 是当前 head 000000000012（prod_seed）。prod 冷启命令见
  CLAUDE.md §15.2，已同步追加 013、014 两步。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000013"
down_revision: Union[str, None] = "000000000012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 加列
    op.add_column(
        "t_customer",
        sa.Column(
            "serial_prefix",
            sa.String(length=1),
            nullable=True,
            comment="一级客户序列号前缀（A-Z）；叶子客户 NULL",
        ),
    )

    # 2. 历史回填：现有 3 个一级客户按 PARENT_TO_CODE 兜底。
    # 注意：必须先于部分唯一索引的创建，避免与未来插入产生瞬时不唯一。
    op.execute(
        sa.text(
            "UPDATE t_customer SET serial_prefix='F' "
            "WHERE name='法拉电子' AND parent_id IS NULL AND deleted_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE t_customer SET serial_prefix='L' "
            "WHERE name='路达' AND parent_id IS NULL AND deleted_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE t_customer SET serial_prefix='H' "
            "WHERE name='宏发' AND parent_id IS NULL AND deleted_at IS NULL"
        )
    )

    # 3. check constraint：限定 NULL 或 A-Z 单字符
    op.create_check_constraint(
        "ck_t_customer_serial_prefix_uppercase",
        "t_customer",
        "serial_prefix IS NULL OR serial_prefix ~ '^[A-Z]$'",
    )

    # 4. 部分唯一索引：仅约束未软删的根客户
    op.create_index(
        "uq_t_customer_root_prefix",
        "t_customer",
        ["serial_prefix"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND parent_id IS NULL AND serial_prefix IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_t_customer_root_prefix", table_name="t_customer")
    op.drop_constraint(
        "ck_t_customer_serial_prefix_uppercase",
        "t_customer",
        type_="check",
    )
    op.drop_column("t_customer", "serial_prefix")