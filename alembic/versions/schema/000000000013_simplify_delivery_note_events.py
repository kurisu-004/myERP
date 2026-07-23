"""drop redundant columns from t_delivery_note_event (2026-07-23 Bug 4)

背景：用户要求送货单事件表只记录 4 类事件（创建 / 提交 / 撤回 / 领取）。
原 schema 的 drawing_code / badge_code / scanned_count / expected_count 4 列
仅被 PICKUP_SCANNED 事件使用；PICKUP_SCANNED 已在 enum 中删除（`model/enums.py`
DELIVERY_NOTE_EVENT_TYPE 精简），新代码也不再写。这些列保留会让前端 schema
反复出现噪音字段，故一并 drop。

模型同步：``model/delivery_note_event.py::TDeliveryNoteEvent`` 已删除对应
mapped_column（schema 生成会一致）。

注意：表中已有数据不会丢。downgrade() 重新加回 nullable 列即可（无数据恢复，
列是历史空字段）。

Revision ID: 000000000013
Revises: 000000000012
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000013"
# 单 head 线性拓扑：… 011 → 012 → 013
down_revision: Union[str, None] = "000000000012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("t_delivery_note_event", "drawing_code")
    op.drop_column("t_delivery_note_event", "badge_code")
    op.drop_column("t_delivery_note_event", "scanned_count")
    op.drop_column("t_delivery_note_event", "expected_count")


def downgrade() -> None:
    # 重新加回 4 列；nullable=True（无数据可恢复）。
    op.add_column(
        "t_delivery_note_event",
        sa.Column("drawing_code", sa.String(length=100), nullable=True,
                  comment="扫码时图纸码（serial_no）"),
    )
    op.add_column(
        "t_delivery_note_event",
        sa.Column("badge_code", sa.String(length=50), nullable=True,
                  comment="扫码时工牌码"),
    )
    op.add_column(
        "t_delivery_note_event",
        sa.Column("scanned_count", sa.Integer(), nullable=True,
                  comment="累积扫码次数（仅 PICKUP_SCANNED）"),
    )
    op.add_column(
        "t_delivery_note_event",
        sa.Column("expected_count", sa.Integer(), nullable=True,
                  comment="本单所需扫描总数（仅 PICKUP_SCANNED）"),
    )