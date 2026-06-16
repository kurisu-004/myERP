"""rbac module: extend t_user + 5 new tables

Revision ID: a1b2c3d4e5f6
Revises: cad8a3b29f3a
Create Date: 2026-06-16 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "cad8a3b29f3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 扩展 t_user: 先去掉 PK 约束,才能清掉序列
    op.execute("ALTER TABLE t_user DROP CONSTRAINT t_user_pkey CASCADE")
    op.execute("ALTER TABLE t_user ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS t_user_id_seq")
    op.execute("ALTER TABLE t_user ADD CONSTRAINT t_user_pkey PRIMARY KEY (id)")

    op.add_column("t_user", sa.Column("employee_no", sa.String(length=20), nullable=False, server_default="LEGACY"))
    op.add_column("t_user", sa.Column("password_hash", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("t_user", sa.Column("name", sa.String(length=50), nullable=False, server_default="legacy"))
    op.add_column("t_user", sa.Column("is_active", sa.SmallInteger(), nullable=False, server_default="1"))
    op.add_column("t_user", sa.Column("last_login_at", sa.DateTime(), nullable=True))
    op.add_column("t_user", sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.add_column("t_user", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.add_column("t_user", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("t_user", sa.Column("created_by", sa.BigInteger(), nullable=True))

    op.alter_column("t_user", "employee_no", server_default=None)
    op.alter_column("t_user", "password_hash", server_default=None)
    op.alter_column("t_user", "name", server_default=None)

    # 2) t_user_profile
    op.create_table(
        "t_user_profile",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("id_card", sa.String(length=32), nullable=True),
        sa.Column("gender", sa.String(length=8), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("hire_date", sa.Date(), nullable=True),
        sa.Column("leave_date", sa.Date(), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("wechat", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=100), nullable=True),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("emergency_contact_name", sa.String(length=50), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(length=20), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # 3) t_role
    op.create_table(
        "t_role",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_builtin", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_t_role_code", "t_role", ["code"], unique=True)

    # 4) t_permission
    op.create_table(
        "t_permission",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column("component", sa.String(length=255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["t_permission.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_t_permission_code", "t_permission", ["code"], unique=True)
    op.create_index("idx_t_permission_parent", "t_permission", ["parent_id", "sort_order"])
    op.create_index("idx_t_permission_type", "t_permission", ["type"])

    # 5) t_role_permission
    op.create_table(
        "t_role_permission",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("permission_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["permission_id"], ["t_permission.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["t_role.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6) t_user_role
    op.create_table(
        "t_user_role",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["t_role.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("t_user_role")
    op.drop_table("t_role_permission")
    op.drop_index("idx_t_permission_type", table_name="t_permission")
    op.drop_index("idx_t_permission_parent", table_name="t_permission")
    op.drop_index("uk_t_permission_code", table_name="t_permission")
    op.drop_table("t_permission")
    op.drop_index("uk_t_role_code", table_name="t_role")
    op.drop_table("t_role")
    op.drop_table("t_user_profile")

    op.drop_column("t_user", "created_by")
    op.drop_column("t_user", "deleted_at")
    op.drop_column("t_user", "updated_at")
    op.drop_column("t_user", "created_at")
    op.drop_column("t_user", "last_login_at")
    op.drop_column("t_user", "is_active")
    op.drop_column("t_user", "name")
    op.drop_column("t_user", "password_hash")
    op.drop_column("t_user", "employee_no")

    op.execute("CREATE SEQUENCE t_user_id_seq AS BIGINT")
    op.execute("ALTER TABLE t_user ALTER COLUMN id SET DEFAULT nextval('t_user_id_seq')")
