"""create t_customer and t_part

Revision ID: a1f9c2d8e3b4
Revises:
Create Date: 2026-06-26

注意：
1. 项目约定 **不使用物理外键**。所有跨表引用都是普通列 + 普通索引；
   引用完整性由 service 层校验。
2. 所有表都自带审计字段：created_at / created_by / updated_at / updated_by / deleted_at。
   deleted_at 是软删标记，NULL 表示未删除。
3. t_part 增加 is_urgent 字段用于加急筛选。
4. **不使用 DB 枚举字段**（CLAUDE.md 待补 §9）：t_part.status 用
   `varchar(20)`，取值合法性由 Python `PartStatus` 在 service 层校验。
   注意：早期版本曾用 PostgreSQL `part_status` ENUM，已在后续迁移
   `c3d4e5f6a7b8` 里 ALTER TYPE 转成 varchar 并 DROP TYPE。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f9c2d8e3b4"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # —— t_customer ——
    op.create_table(
        "t_customer",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        # parent_id 是逻辑外键，指向 t_customer.id（无 DB FK 约束）。
        sa.Column("parent_id", sa.BigInteger, nullable=True),
        # —— 审计字段 ——
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_customer_name", "t_customer", ["name"])
    op.create_index("ix_t_customer_parent_id", "t_customer", ["parent_id"])
    op.create_index("ix_t_customer_deleted_at", "t_customer", ["deleted_at"])
    op.create_check_constraint(
        "ck_t_customer_no_self_parent",
        "t_customer",
        "parent_id IS NULL OR parent_id <> id",
    )

    # —— t_part ——
    # 雪花 ID：通过 SQLAlchemy default 注入；DB 层不设置 autoincrement。
    op.create_table(
        "t_part",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("drawing_no", sa.String(length=100), nullable=False),
        sa.Column("applicant_name", sa.String(length=50), nullable=False),
        sa.Column(
            "quantity",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "unit_price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_price",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("planned_delivery_date", sa.Date(), nullable=False),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        # 不使用 DB ENUM：varchar(20) + Python PartStatus 校验。
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "is_urgent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否加急",
        ),
        # customer_id 是逻辑外键，指向 t_customer.id（无 DB FK 约束）。
        sa.Column("customer_id", sa.BigInteger, nullable=False),
        # —— 审计字段 ——
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_part_name", "t_part", ["name"])
    op.create_index("ix_t_part_drawing_no", "t_part", ["drawing_no"])
    op.create_index("ix_t_part_customer_id", "t_part", ["customer_id"])
    op.create_index("ix_t_part_status", "t_part", ["status"])
    op.create_index("ix_t_part_is_urgent", "t_part", ["is_urgent"])
    op.create_index("ix_t_part_request_date", "t_part", ["request_date"])
    op.create_index(
        "ix_t_part_planned_delivery_date", "t_part", ["planned_delivery_date"]
    )
    op.create_index("ix_t_part_deleted_at", "t_part", ["deleted_at"])
    # 组合索引：高频查询"某客户某状态下的零件按交期排序"
    op.create_index(
        "ix_t_part_customer_status_delivery",
        "t_part",
        ["customer_id", "status", "planned_delivery_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_t_part_customer_status_delivery", table_name="t_part")
    op.drop_index("ix_t_part_deleted_at", table_name="t_part")
    op.drop_index("ix_t_part_planned_delivery_date", table_name="t_part")
    op.drop_index("ix_t_part_request_date", table_name="t_part")
    op.drop_index("ix_t_part_is_urgent", table_name="t_part")
    op.drop_index("ix_t_part_status", table_name="t_part")
    op.drop_index("ix_t_part_customer_id", table_name="t_part")
    op.drop_index("ix_t_part_drawing_no", table_name="t_part")
    op.drop_index("ix_t_part_name", table_name="t_part")
    op.drop_table("t_part")

    op.drop_index("ix_t_customer_deleted_at", table_name="t_customer")
    op.drop_constraint(
        "ck_t_customer_no_self_parent", "t_customer", type_="check"
    )
    op.drop_index("ix_t_customer_parent_id", table_name="t_customer")
    op.drop_index("ix_t_customer_name", table_name="t_customer")
    op.drop_table("t_customer")