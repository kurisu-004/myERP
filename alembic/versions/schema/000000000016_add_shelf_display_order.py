"""add_shelf_display_order: t_shelf 加 display_order 列 + 索引 + 3 架 backfill

Revision ID: 000000000016
Revises: 000000000015
Create Date: 2026-07-10

要点：
- 共享 HMI 多货架场景（plan 文件 glowing-giggling-liskov.md）：前端卡片网格
  picker 按"进门右手边第一架"这种物理顺序铺，而不是按 code 字母序。
- 新增 `display_order INTEGER NOT NULL DEFAULT 0` 列；manager 在
  `frontend/src/views/shelves/ShelfList.vue` 后台可拖拽重排（手填数值即可，
  本迁移不引入拖拽 UI —— 仅打地基）。
- 索引：部分索引 `ix_t_shelf_display_order` 仅约束未软删行，避免软删行
  长期占用排序位。
- 历史 backfill：dev seed 的 3 架按"生产 1 / 生产 2 / 品检"顺序给
  display_order = 1, 2, 3。prod 库没有预置 t_shelf 行，backfill 不动。
- 不引入物理外键（与 CLAUDE.md §1 一致）。
- downgrade：drop column + drop index；backfill 数值随列 drop 消失。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000000000016"
down_revision: Union[str, None] = "000000000015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 加列：NOT NULL DEFAULT 0，老行直接拿到 default 0
    op.add_column(
        "t_shelf",
        sa.Column(
            "display_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="物理顺序（0=未设置；manager 在 ShelfList 后台手填）",
        ),
    )
    # 2) 部分索引：仅未软删行（与 ix_t_shelf_deleted_at 的 partial 思路一致）
    op.create_index(
        "ix_t_shelf_display_order",
        "t_shelf",
        ["display_order", "code"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 3) 历史 backfill：dev seed 的 3 架（PROD-A1 → 1 / PROD-B1 → 2 / INSP-I1 → 3）
    #    prod 库无 t_shelf 行，UPDATE 影响 0 行 —— 无副作用。
    op.execute(
        sa.text(
            """
            UPDATE t_shelf SET display_order = CASE code
                WHEN 'PROD-A1' THEN 1
                WHEN 'PROD-B1' THEN 2
                WHEN 'INSP-I1' THEN 3
                ELSE 0
            END
            WHERE code IN ('PROD-A1', 'PROD-B1', 'INSP-I1')
              AND deleted_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_t_shelf_display_order", table_name="t_shelf")
    op.drop_column("t_shelf", "display_order")
