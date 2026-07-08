"""dev_shelf_process_seed: 货架 ↔ 工序 多对多种子

Revision ID: 000000000011
Revises: 000000000010
Create Date: 2026-07-07

说明（dev_data 类别种子）：
- 所有已有 PRODUCTION 货架自动映射全部 INHOUSE 工序，
  保证存量兼容（工人放回时工序下拉框不为空）。
- 品检货架不自动映射工序（品检区不需要放回工序选择）。
- DDL 部分（t_shelf_process 表）已拆到 `schema/000000000004_shelf_process_table.py`。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000011"
down_revision: Union[str, None] = "000000000010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()
    shelf_rows = bind.execute(
        sa.text(
            "SELECT id FROM t_shelf WHERE zone = 'PRODUCTION' AND deleted_at IS NULL"
        )
    ).fetchall()
    process_rows = bind.execute(
        sa.text(
            "SELECT id FROM t_process WHERE category = 'INHOUSE' AND deleted_at IS NULL ORDER BY sort_order, id"
        )
    ).fetchall()

    shelf_ids = [r[0] for r in shelf_rows]
    process_ids = [r[0] for r in process_rows]

    if shelf_ids and process_ids:
        for sid in shelf_ids:
            for idx, pid in enumerate(process_ids):
                bind.execute(
                    sa.text(
                        "INSERT INTO t_shelf_process (id, shelf_id, process_id, sort_order) "
                        "VALUES (:id, :shelf_id, :process_id, :sort_order)"
                    ),
                    {
                        "id": new_id(),
                        "shelf_id": sid,
                        "process_id": pid,
                        "sort_order": idx,
                    },
                )


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    op.execute(
        "DELETE FROM t_shelf_process WHERE deleted_at IS NULL"
    )
