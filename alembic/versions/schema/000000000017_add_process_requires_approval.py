"""add requires_approval to t_process for direct-send OUTSOURCE flow (2026-07-28)

- 新增 `requires_approval` Boolean 列：
  * True  = 该 OUTSOURCE 工序走原有报价 + MANAGER 审批 + 发送流程（默认；保持现状）
  * False = 该 OUTSOURCE 工序可绕过报价审批，由 CLERK/INSPECTOR 在「零件位于
            C2 / 生产/外协 C2 货架」的前提下直接发送；MANAGER 仍可用
  * INHOUSE 工序不进入外协流程，字段值无业务含义（迁移后归一为 False）
- `server_default=true` 给现存 OUTSOURCE 工序回填 True，行为不变；
  INHOUSE 由后续 UPDATE 步骤归一为 False
- 接在 000000000016（C2 货架 seed）之后，保持单 head 线性拓扑

Revision ID: 000000000017
Revises: 000000000016
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000017"
down_revision: Union[str, None] = "000000000016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 添加 requires_approval 列（NOT NULL + server_default=true）
    op.add_column(
        "t_process",
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="外协工序是否需要报价审批",
        ),
    )

    # 2) 把既有 INHOUSE 工序归一为 False（与新规则一致：
    #    INHOUSE 不进入外协流程，requires_approval 仅占位）
    op.execute(
        "UPDATE t_process SET requires_approval = false "
        "WHERE category = 'INHOUSE'"
    )


def downgrade() -> None:
    op.drop_column("t_process", "requires_approval")