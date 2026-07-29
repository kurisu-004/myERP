"""allow zero-price placeholders for DIRECT outsource records (2026-07-29)

DIRECT send_to_outsource creates an approved quote with price=0 so clerks can
fill the actual unit price on the reconciliation page.  Relax the legacy quote
constraint while keeping user-facing create/update schemas at price > 0.

Revision ID: 000000000021
Revises: 000000000020
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "000000000021"
down_revision: Union[str, None] = "000000000020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_t_outsource_quote_price_positive",
        "t_outsource_quote",
        type_="check",
    )
    op.create_check_constraint(
        "ck_t_outsource_quote_price_positive",
        "t_outsource_quote",
        "price >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_t_outsource_quote_price_positive",
        "t_outsource_quote",
        type_="check",
    )
    op.create_check_constraint(
        "ck_t_outsource_quote_price_positive",
        "t_outsource_quote",
        "price > 0",
    )
