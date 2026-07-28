"""outsource quote 升级为外协统一事实表 (2026-07-29)

PR-H 2026-07-29: 把 t_outsource_quote 扩为外协全生命周期记录（不再只是"报价审批流"）：
- 新增 `sent_at` / `received_at` / `quantity` / `is_billed` 4 列
  （`version` 已由 AuditMixin 提供，不重复添加）
- 新增 2 个二级索引：(outsource_company_id, sent_at) + (outsource_company_id, received_at)
  —— 对账页按公司+时间范围/排序的核心查询路径
- 数据迁移：旧 USED 状态记录（已发出）自动视为 RECEIVED，
  received_at 用 reviewed_at 兜底。USABLE 字段保留兼容，但不参与新业务流程。
- 不删 t_outsource_quote.status 上的 'USED' 值（兼容历史数据）；
  新增 'OUTSOURCING' / 'RECEIVED' / 'BILLED' 状态由 application code 写入。
- 不加 DB FK（CLAUDE.md §1）

接在 000000000018 之后，保持单 head 线性拓扑。

Revision ID: 000000000019
Revises: 000000000018
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000019"
down_revision: Union[str, None] = "000000000018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 5 个新列
    op.add_column(
        "t_outsource_quote",
        sa.Column("sent_at", sa.DateTime(), nullable=True,
                  comment="发送时间（send_to_outsource 触发时写入）"),
    )
    op.add_column(
        "t_outsource_quote",
        sa.Column("received_at", sa.DateTime(), nullable=True,
                  comment="接收时间（receive_from_outsource 触发时写入）"),
    )
    op.add_column(
        "t_outsource_quote",
        sa.Column("quantity", sa.Integer(), nullable=True,
                  comment="本次发送数量 snapshot；可能与 t_part.quantity 不同"),
    )
    op.add_column(
        "t_outsource_quote",
        sa.Column(
            "is_billed", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
            comment="对账标记（与状态 RECEIVED/BILLED 配套）",
        ),
    )
    # 注：version 列已由 AuditMixin 提供（CLAUDE.md §12），本迁移不重复添加。

    # 2. 二级索引（对账页查 (company, sent_at) / (company, received_at)）
    op.create_index(
        "ix_t_outsource_quote_company_sent_at",
        "t_outsource_quote",
        ["outsource_company_id", "sent_at"],
    )
    op.create_index(
        "ix_t_outsource_quote_company_received_at",
        "t_outsource_quote",
        ["outsource_company_id", "received_at"],
    )

    # 3. 数据迁移：旧 USED 视为 RECEIVED，received_at 兜底 reviewed_at
    # SQLAlchemy op.execute 直接 SQL（数据回填，不动 schema）
    op.execute("""
        UPDATE t_outsource_quote
        SET received_at = COALESCE(reviewed_at, updated_at)
        WHERE status = 'USED' AND received_at IS NULL
    """)

    # 4. CHECK 约束：放开 status 枚举（migration 000000000003 建表时只含 5 个旧状态；
    #    ORM 已声明新枚举，这里同步 DB 约束；PG 不支持 ALTER CONSTRAINT，drop + re-add）
    op.drop_constraint(
        "ck_t_outsource_quote_status", "t_outsource_quote", type_="check",
    )
    op.create_check_constraint(
        "ck_t_outsource_quote_status",
        "t_outsource_quote",
        "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED',"
        "'OUTSOURCING','RECEIVED','BILLED','USED')",
    )


def downgrade() -> None:
    # CHECK 约束回滚（只含旧 5 状态）
    op.drop_constraint(
        "ck_t_outsource_quote_status", "t_outsource_quote", type_="check",
    )
    op.create_check_constraint(
        "ck_t_outsource_quote_status",
        "t_outsource_quote",
        "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','USED')",
    )
    op.drop_index("ix_t_outsource_quote_company_received_at",
                  table_name="t_outsource_quote")
    op.drop_index("ix_t_outsource_quote_company_sent_at",
                  table_name="t_outsource_quote")
    op.drop_column("t_outsource_quote", "is_billed")
    op.drop_column("t_outsource_quote", "quantity")
    op.drop_column("t_outsource_quote", "received_at")
    op.drop_column("t_outsource_quote", "sent_at")
