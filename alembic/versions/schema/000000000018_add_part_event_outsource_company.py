"""add outsource_company_id to t_part_event for outsource billing audit (2026-07-28)

- 新增 `outsource_company_id` BigInteger NULL 列：
  * SENT_TO_OUTSOURCE 时填入：把零件送给哪家外协公司
  * RECEIVED_FROM_OUTSOURCE 时填入：从哪家外协公司回收
  * 其他事件类型（CREATED / PICKED_UP / INSPECTED 等）保持 NULL
- 新增部分索引 `ix_t_part_event_outsource_company_id`：仅在
  outsource_company_id IS NOT NULL 时生效，避免 99% 的非外协事件占索引空间
- 不加 DB FK（CLAUDE.md §1 约定：跨表引用在 service 层校验）
- 接在 000000000017（requires_approval）之后，保持单 head 线性拓扑

Revision ID: 000000000018
Revises: 000000000017
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000018"
down_revision: Union[str, None] = "000000000017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_part_event",
        sa.Column(
            "outsource_company_id",
            sa.BigInteger(),
            nullable=True,
            comment="SENT_TO_OUTSOURCE / RECEIVED_FROM_OUTSOURCE 时填入；外协对账按此列聚合",
        ),
    )
    # 注：t_part_event 是 EventTimestampMixin（append-only），无 deleted_at 列。
    # 部分索引谓词只需 outsource_company_id IS NOT NULL。
    op.create_index(
        "ix_t_part_event_outsource_company_id",
        "t_part_event",
        ["outsource_company_id"],
        postgresql_where=sa.text("outsource_company_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_t_part_event_outsource_company_id", table_name="t_part_event",
    )
    op.drop_column("t_part_event", "outsource_company_id")