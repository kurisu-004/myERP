"""add t_part.serial_no with per-customer cycle (F1000..F9999 / L1000..L9999)

Revision ID: b2c3d4e5f6a7
Revises: a1f9c2d8e3b4
Create Date: 2026-06-28

说明：
- 每条 PENDING/IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED/REPAIRING
  状态的 t_part 记录都有一个 serial_no 形如 "F1000" / "L1234"。
- COMPLETED / CANCELLED 时 serial_no 置 NULL，号被释放并可被同客户
  下一次新 PENDING 复用（找最小可用号，1000-9999 循环）。
- 已存在的活跃记录在本次迁移里按 planned_delivery_date 顺序回填：
  法拉电子 → F1000, F1001, ...；路达 → L1000, L1001, ...
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1f9c2d8e3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 加列
    op.add_column(
        "t_part",
        sa.Column("serial_no", sa.String(length=8), nullable=True),
    )
    # 2. 部分唯一索引（仅对非 NULL），保证 serial_no 全局不重复
    op.create_index(
        "uk_t_part_serial_no",
        "t_part",
        ["serial_no"],
        unique=True,
        postgresql_where=sa.text("serial_no IS NOT NULL"),
    )
    # 3. 回填：纯 SQL，避免 alembic 异步 event loop 嵌套
    op.execute(
        sa.text(
            """
            WITH active AS (
                SELECT
                    p.id,
                    parent.name AS parent_name,
                    ROW_NUMBER() OVER (
                        PARTITION BY parent.name
                        ORDER BY p.planned_delivery_date ASC, p.created_at ASC, p.id ASC
                    ) AS rn
                FROM t_part p
                JOIN t_customer c ON p.customer_id = c.id
                JOIN t_customer parent ON c.parent_id = parent.id
                WHERE p.status NOT IN ('COMPLETED', 'CANCELLED')
                  AND p.deleted_at IS NULL
                  AND p.serial_no IS NULL
                  AND parent.name IN ('法拉电子', '路达')
            )
            UPDATE t_part
            SET serial_no = (
                CASE active.parent_name
                    WHEN '法拉电子' THEN 'F' || (active.rn + 999)
                    WHEN '路达'      THEN 'L' || (active.rn + 999)
                END
            )
            FROM active
            WHERE t_part.id = active.id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("uk_t_part_serial_no", table_name="t_part")
    op.drop_column("t_part", "serial_no")