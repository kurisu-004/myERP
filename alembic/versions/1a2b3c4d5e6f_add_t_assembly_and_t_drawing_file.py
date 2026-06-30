"""add t_assembly and t_drawing_file; t_part.assembly_id

Revision ID: 1a2b3c4d5e6f
Revises: f1a2b3c4d5e6
Create Date: 2026-06-30

说明：
- 新增 t_assembly 表：装配件（总装图对应的逻辑分组），与 t_part 一对多。
  t_part.assembly_id 指向 t_assembly.id；NULL 表示该零件不属于任何装配件。
- 新增 t_drawing_file 表：COS 上的图纸文件元数据。
  - part_id 与 assembly_id 二选一非空（DB CHECK 约束保证）。
  - 多页 PDF 整本上传一次；装配件与各子件各占一行，page_index 区分。
  - upload_status: PENDING / READY / FAILED（与 t_part.status 一致用 String(20)，
    不用 DB ENUM，避免与 CLAUDE.md 风格冲突）。
- t_part.assembly_id 复合索引 (assembly_id, status) 支撑"按装配体查所有子件
  状态分布"看板。
- 不使用物理外键（CLAUDE.md §1）；t_part.assembly_id 与 t_drawing_file.part_id /
  assembly_id 都走 service 层校验目标存在 / 未软删。
- 审计字段顺序与 model/audit.py:AuditMixin 严格对齐，避免 alembic
  compare_column_order 触发虚假重排迁移。
- 删除装配件走 service 级联软删，不在 DB 层加 FK CASCADE。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. t_part 加 assembly_id 字段 + 索引
    op.add_column(
        "t_part",
        sa.Column(
            "assembly_id",
            sa.BigInteger(),
            nullable=True,
            comment="逻辑外键 → t_assembly.id；NULL = 非装配件子件",
        ),
    )
    op.create_index(
        "ix_t_part_assembly_id",
        "t_part",
        ["assembly_id"],
    )
    op.create_index(
        "ix_t_part_assembly_id_status",
        "t_part",
        ["assembly_id", "status"],
    )

    # 2. t_assembly 表
    op.create_table(
        "t_assembly",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "drawing_no",
            sa.String(length=100),
            nullable=False,
            comment="总图图号（如 E42FX1020107101）",
        ),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
            comment="装配体名称（如 精研挡料座）",
        ),
        sa.Column("applicant_name", sa.String(length=50), nullable=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            nullable=False,
            comment="逻辑外键 → t_customer.id 叶子节点",
        ),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("planned_delivery_date", sa.Date(), nullable=False),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column(
            "is_urgent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="PENDING（默认）/ COMPLETED",
        ),
        # —— 审计字段（与 AuditMixin 对齐）——
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_assembly_drawing_no", "t_assembly", ["drawing_no"])
    op.create_index("ix_t_assembly_customer_id", "t_assembly", ["customer_id"])
    op.create_index("ix_t_assembly_status", "t_assembly", ["status"])
    op.create_index(
        "ix_t_assembly_planned_delivery",
        "t_assembly",
        ["planned_delivery_date"],
    )
    op.create_index(
        "ix_t_assembly_customer_status",
        "t_assembly",
        ["customer_id", "status"],
    )
    op.create_index("ix_t_assembly_deleted_at", "t_assembly", ["deleted_at"])

    # 3. t_drawing_file 表
    op.create_table(
        "t_drawing_file",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "part_id",
            sa.BigInteger(),
            nullable=True,
            comment="逻辑外键 → t_part.id；NULL 表示挂在装配件上",
        ),
        sa.Column(
            "assembly_id",
            sa.BigInteger(),
            nullable=True,
            comment="逻辑外键 → t_assembly.id；NULL 表示挂在子件上",
        ),
        sa.Column(
            "file_type",
            sa.String(length=20),
            nullable=False,
            comment="PDF / STEP / DWG / DXF",
        ),
        sa.Column(
            "object_key",
            sa.String(length=500),
            nullable=False,
            comment="COS 对象 key",
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column(
            "page_index",
            sa.Integer(),
            nullable=True,
            comment="多页 PDF 中该行指向的页码；非 PDF 场景 NULL",
        ),
        sa.Column(
            "upload_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'READY'"),
            comment="PENDING / READY / FAILED",
        ),
        # —— 审计字段（与 AuditMixin 对齐）——
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        # part_id 与 assembly_id 二选一非空（XOR）
        sa.CheckConstraint(
            "(part_id IS NOT NULL) <> (assembly_id IS NOT NULL)",
            name="ck_t_drawing_file_owner_xor",
        ),
    )
    op.create_index("ix_t_drawing_file_part_id", "t_drawing_file", ["part_id"])
    op.create_index(
        "ix_t_drawing_file_assembly_id", "t_drawing_file", ["assembly_id"]
    )
    op.create_index(
        "ix_t_drawing_file_part_type",
        "t_drawing_file",
        ["part_id", "file_type"],
    )
    op.create_index(
        "ix_t_drawing_file_assembly_type",
        "t_drawing_file",
        ["assembly_id", "file_type"],
    )
    op.create_index(
        "ix_t_drawing_file_deleted_at", "t_drawing_file", ["deleted_at"]
    )


def downgrade() -> None:
    # 注意：先 drop 引用 t_part.assembly_id 的索引，再 drop 列
    op.drop_index("ix_t_part_assembly_id_status", table_name="t_part")
    op.drop_index("ix_t_part_assembly_id", table_name="t_part")
    op.drop_column("t_part", "assembly_id")

    op.drop_index("ix_t_drawing_file_deleted_at", table_name="t_drawing_file")
    op.drop_index("ix_t_drawing_file_assembly_type", table_name="t_drawing_file")
    op.drop_index("ix_t_drawing_file_part_type", table_name="t_drawing_file")
    op.drop_index("ix_t_drawing_file_assembly_id", table_name="t_drawing_file")
    op.drop_index("ix_t_drawing_file_part_id", table_name="t_drawing_file")
    op.drop_table("t_drawing_file")

    op.drop_index("ix_t_assembly_deleted_at", table_name="t_assembly")
    op.drop_index("ix_t_assembly_customer_status", table_name="t_assembly")
    op.drop_index("ix_t_assembly_planned_delivery", table_name="t_assembly")
    op.drop_index("ix_t_assembly_status", table_name="t_assembly")
    op.drop_index("ix_t_assembly_customer_id", table_name="t_assembly")
    op.drop_index("ix_t_assembly_drawing_no", table_name="t_assembly")
    op.drop_table("t_assembly")