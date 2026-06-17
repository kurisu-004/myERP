"""switch to timezone aware datetime

Revision ID: 0b6563b9f202
Revises: b2c3d4e5f6a7
Create Date: 2026-06-17 14:50:30.648168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0b6563b9f202'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TZ_COLUMNS: list[tuple[str, str, bool]] = [
    ("t_permission", "created_at", False),
    ("t_permission", "updated_at", False),
    ("t_permission", "deleted_at", True),
    ("t_role", "created_at", False),
    ("t_role", "updated_at", False),
    ("t_role", "deleted_at", True),
    ("t_role_permission", "created_at", False),
    ("t_role_permission", "updated_at", False),
    ("t_role_permission", "deleted_at", True),
    ("t_user", "last_login_at", True),
    ("t_user", "created_at", False),
    ("t_user", "updated_at", False),
    ("t_user", "deleted_at", True),
    ("t_user_profile", "created_at", False),
    ("t_user_profile", "updated_at", False),
    ("t_user_profile", "deleted_at", True),
    ("t_user_role", "created_at", False),
    ("t_user_role", "updated_at", False),
    ("t_user_role", "deleted_at", True),
]


def upgrade() -> None:
    """把所有时间列改为 TIMESTAMP WITH TIME ZONE。

    PostgreSQL：应用层传入带 tzinfo 的 aware datetime，
    DB 自动规范化为 UTC 存储。
    """
    for table, column, nullable in _TZ_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=nullable,
            existing_server_default=sa.text("CURRENT_TIMESTAMP") if not nullable else None,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """回退为不带时区的时间列。"""
    for table, column, nullable in reversed(_TZ_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=nullable,
            existing_server_default=sa.text("CURRENT_TIMESTAMP") if not nullable else None,
        )