"""add_outsource_quote：t_outsource_quote + t_outsource_quote_event（2026-07-16 新增）

Revision ID: 000000000003
Revises: 000000000001
Create Date: 2026-07-16

说明：
- 外协报价体系：文员录入报价 → 经理审批 → 通过后零件可发送外协（与 send_to_outsource 防御闸配合）。
- 主表 `t_outsource_quote`：业务主表，审计 + OCC；
  - status 枚举 DRAFT / SUBMITTED / APPROVED / REJECTED / USED；
  - price 单件单价 NUMERIC(12,2)，必须 >0；
  - 同一 (part_id, company_id, process_id) 仅允许一条非 REJECTED 活跃行（partial unique 索引）。
- 事件表 `t_outsource_quote_event`：append-only 审计（只 created_at，无 OCC）。
- 不使用物理外键；不在 DB 层用 ENUM；status / event_type 用 varchar。
- down_revision = "000000000001"（schema 层 head，不指 prod_data/002），
  拓扑多 head：`schema/001 + schema/003 + prod_data/002`。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000000000003"
down_revision: Union[str, None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _version_col() -> sa.Column:
    """乐观锁 version 列工厂（与 schema/000000000001 一致）。"""
    return sa.Column(
        "version", sa.Integer(), nullable=False,
        server_default=sa.text("0"),
        comment="乐观锁版本号；每次 UPDATE 自增；冲突抛 BIZ_VERSION_CONFLICT 409",
    )


def upgrade() -> None:
    # ============================================================
    # 1) t_outsource_quote：外协报价单（业务主表 + AuditMixin + OCC）
    # ============================================================
    op.create_table(
        "t_outsource_quote",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "part_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_part.id",
        ),
        sa.Column(
            "outsource_company_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_outsource_company.id",
        ),
        sa.Column(
            "process_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_process.id（必须 OUTSOURCE 类别）",
        ),
        sa.Column(
            "price", sa.Numeric(12, 2), nullable=False,
            comment="单件单价（CNY）",
        ),
        sa.Column(
            "note", sa.String(length=500), nullable=True,
            comment="备注",
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default=sa.text("'DRAFT'"),
            comment="DRAFT / SUBMITTED / APPROVED / REJECTED / USED",
        ),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "review_note", sa.String(length=500), nullable=True,
            comment="审批意见（reject 必填）",
        ),
        # —— 乐观锁 version ——
        _version_col(),
        # 审计字段（顺序与 AuditMixin 对齐）
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "price > 0",
            name="ck_t_outsource_quote_price_positive",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','USED')",
            name="ck_t_outsource_quote_status",
        ),
    )
    # 普通索引
    op.create_index(
        "ix_t_outsource_quote_part_id",
        "t_outsource_quote", ["part_id"],
    )
    op.create_index(
        "ix_t_outsource_quote_outsource_company_id",
        "t_outsource_quote", ["outsource_company_id"],
    )
    op.create_index(
        "ix_t_outsource_quote_process_id",
        "t_outsource_quote", ["process_id"],
    )
    op.create_index(
        "ix_t_outsource_quote_status",
        "t_outsource_quote", ["status"],
    )
    op.create_index(
        "ix_t_outsource_quote_deleted_at",
        "t_outsource_quote", ["deleted_at"],
    )
    # 同一 (part, company, process) 非 REJECTED 活跃行 partial unique
    op.create_index(
        "uq_t_outsource_quote_active_tuple",
        "t_outsource_quote",
        ["part_id", "outsource_company_id", "process_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND status IN ('DRAFT','SUBMITTED','APPROVED','USED')"
        ),
    )

    # ============================================================
    # 2) t_outsource_quote_event：事件流（append-only，无 OCC）
    # ============================================================
    op.create_table(
        "t_outsource_quote_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "quote_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_outsource_quote.id",
        ),
        sa.Column(
            "event_type", sa.String(length=32), nullable=False,
            comment="CREATED / EDITED / SUBMITTED / APPROVED / REJECTED / USED",
        ),
        sa.Column(
            "from_status", sa.String(length=16), nullable=True,
            comment="状态机前态",
        ),
        sa.Column(
            "to_status", sa.String(length=16), nullable=True,
            comment="状态机后态",
        ),
        sa.Column(
            "note", sa.String(length=500), nullable=True,
            comment="事件备注",
        ),
        sa.Column(
            "created_by", sa.BigInteger(), nullable=True,
            comment="操作人 user id",
        ),
        # EventTimestampMixin（只要 created_at，无 version / updated_at / deleted_at）
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_t_outsource_quote_event_quote_id",
        "t_outsource_quote_event", ["quote_id"],
    )
    op.create_index(
        "ix_t_outsource_quote_event_created_at",
        "t_outsource_quote_event", ["created_at"],
    )


def downgrade() -> None:
    # drop 顺序无依赖（无物理外键）
    op.drop_index("ix_t_outsource_quote_event_created_at", table_name="t_outsource_quote_event")
    op.drop_index("ix_t_outsource_quote_event_quote_id", table_name="t_outsource_quote_event")
    op.drop_table("t_outsource_quote_event")

    op.drop_index("uq_t_outsource_quote_active_tuple", table_name="t_outsource_quote")
    op.drop_index("ix_t_outsource_quote_deleted_at", table_name="t_outsource_quote")
    op.drop_index("ix_t_outsource_quote_status", table_name="t_outsource_quote")
    op.drop_index("ix_t_outsource_quote_process_id", table_name="t_outsource_quote")
    op.drop_index("ix_t_outsource_quote_outsource_company_id", table_name="t_outsource_quote")
    op.drop_index("ix_t_outsource_quote_part_id", table_name="t_outsource_quote")
    op.drop_table("t_outsource_quote")
