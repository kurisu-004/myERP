"""add has_been_repaired flag to t_part + t_part_batch (2026-08-04)

「返修接收」功能（PR-M）：新增返修件标识列。

- `t_part.has_been_repaired` 与 `t_part_batch.has_been_repaired`：
  Boolean NOT NULL DEFAULT FALSE。
- `start_repair` 在状态机回调执行前由 service 层把 active part + 当前批次
  同步置 `True`；`complete_repair` 走生产架 / 走品检架均不再清。
- 列保留到工单 COMPLETED / CANCELLED 之后；列表 / 卡片 / 详情显示 el-tag「返修」。

接在 `000000000027`（update_repair_receive_sort_order）之后，保持单 head 线性拓扑。（原 revision 025 与 prod_data/000000000025_clear_deactivated_worker_deleted_at 撞号，改号 028。）

Revision ID: 000000000028
Revises: 000000000027
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000028"
# 单 head 线性拓扑：… 020 → 022 → 023 → 024 → 025（新增返修标记）
down_revision: Union[str, None] = "000000000027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_part",
        sa.Column(
            "has_been_repaired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="该工单是否经历过返修（返修件标识，贯穿到 COMPLETED/CANCELLED）",
        ),
    )
    op.add_column(
        "t_part_batch",
        sa.Column(
            "has_been_repaired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="本批次是否经历过返修（与 t_part.has_been_repaired 同步写入）",
        ),
    )
    # 不为该列加索引（仅展示用途；列表过滤仍走 status + planned_delivery_date）


def downgrade() -> None:
    op.drop_column("t_part_batch", "has_been_repaired")
    op.drop_column("t_part", "has_been_repaired")
