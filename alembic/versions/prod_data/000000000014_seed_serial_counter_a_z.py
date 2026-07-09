"""seed_serial_counter_a_z: 把 t_serial_counter 预置 A-Z 全 26 行

Revision ID: 000000000014
Revises: 000000000013
Create Date: 2026-07-09

要点：
- 历史上 t_serial_counter 仅在 dev_data/000000000006 预置 L/F/H 三行；
  任何新增一级客户若带新字母，acquire_serial 会抛 20108 BIZ_SERIAL_PREFIX_UNKNOWN。
- 本迁移把 26 行 A-Z 一次性预置，counter 全部 0；后续 create_part /
  create_assembly / upload_total_pdf / add_child 任意字母可用。
- ON CONFLICT (prefix) DO NOTHING 保证幂等：
  - 在已 seed L/F/H 的 dev 库上跑 → 补齐缺失的 23 行；
  - 在全新 prod 库上跑 → 插入全 26 行。
- downgrade 不删：t_serial_counter 是基础设施，down 时保留避免误删生产数据
  （与 prod_seed 的 down 风格保持一致）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000014"
down_revision: Union[str, None] = "000000000013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "SELECT chr(ascii('A') + i), 0 "
            "FROM generate_series(0, 25) i "
            "ON CONFLICT (prefix) DO NOTHING"
        )
    )


def downgrade() -> None:
    # t_serial_counter 是基础设施；down 时保留 26 行不动。
    pass