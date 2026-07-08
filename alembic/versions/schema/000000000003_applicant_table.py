"""applicant_table: 新增 t_applicant 表（DDL 部分）

Revision ID: 000000000003
Revises: 000000000002
Create Date: 2026-07-06

说明：
- 仅 DDL：t_applicant 表（申请人：姓名 + 所属一级客户，多对一）。
  * `customer_id` 普通列 + 普通索引（DB 层无物理外键，遵守项目约定 1）。
  * `name` 50 字符内，与 customer_id 联合唯一（同一一级客户下不允许重名，
    跨客户允许；软删后允许重建）—— 用 partial unique index 实现。
- 种子部分（客户管理 / 客户一览 / 申请人一览 菜单 + MANAGER/CLERK role_menu）
  已拆到 `dev_data/000000000009_dev_customer_seed.py`。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "000000000003"
down_revision: Union[str, None] = "000000000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
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


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_t_applicant_name_customer_active")
    op.drop_index("ix_t_applicant_deleted_at", table_name="t_applicant")
    op.drop_index("ix_t_applicant_name", table_name="t_applicant")
    op.drop_index("ix_t_applicant_customer_id", table_name="t_applicant")
    op.drop_table("t_applicant")
