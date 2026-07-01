"""init_schema: create all tables, columns, indexes, checks, triggers

Revision ID: 000000000001
Revises:
Create Date: 2026-07-01

说明：
- 把原本的 7 个 schema 迁移（a1f9c2d8e3b4 / b2c3d4e5f6a7 / c3d4e5f6a7b8 /
  d4e5f6a7b8c9 / e5f6a7b8c9d0 / f1a2b3c4d5e6 / 1a2b3c4d5e6f）整合为一个。
- **fresh-install only**：本迁移假定数据库为空（无 enum 列、无旧 schema）。
  整段 "DROP TYPE part_status / enum→varchar 转换" 路径不再需要——
  因为本迁移从一开始就建 `varchar(20)`，不经过 enum。
- **不使用物理外键**（CLAUDE.md §1）：所有跨表引用都是普通列 + 普通索引。
- **不使用 DB ENUM**（CLAUDE.md 待补 §9）：t_part.status / t_assembly.status
  全部用 `varchar(20)`，取值合法性由 Python Enum 在 service 层校验。
- 审计字段（created_at / created_by / updated_at / updated_by / deleted_at）
  列顺序与 model/audit.py:AuditMixin 严格对齐，避免 alembic
  compare_column_order 触发虚假重排迁移。
- t_part_event 是事件流（append-only），继承 EventTimestampMixin，
  故意不建 updated_at / 操作人 / 软删。
- 软删约定：默认查询 `deleted_at IS NULL`；repository 已统一加。
- `t_serial_counter` 启动时种 3 行 L/F/H，counter=0。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "000000000001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =================================================================
    # 1) t_customer：邻接表，1 级 / 2 级客户
    # =================================================================
    op.create_table(
        "t_customer",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        # parent_id 是逻辑外键，指向 t_customer.id（无 DB FK 约束）。
        sa.Column("parent_id", sa.BigInteger, nullable=True),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_customer_name", "t_customer", ["name"])
    op.create_index("ix_t_customer_parent_id", "t_customer", ["parent_id"])
    op.create_index("ix_t_customer_deleted_at", "t_customer", ["deleted_at"])
    op.create_check_constraint(
        "ck_t_customer_no_self_parent",
        "t_customer",
        "parent_id IS NULL OR parent_id <> id",
    )

    # =================================================================
    # 2) t_part：零件订单（不含 serial_no / current_worker_id / released_at / assembly_id）
    #    后面用 ALTER 加上（保持与原 b2c3d4... + c3d4e5... + 1a2b3c4... 的演进路径对应）
    # =================================================================
    # 雪花 ID：通过 SQLAlchemy default 注入；DB 层不设置 autoincrement。
    op.create_table(
        "t_part",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("drawing_no", sa.String(length=100), nullable=False),
        sa.Column("applicant_name", sa.String(length=50), nullable=False),
        sa.Column(
            "quantity", sa.Integer, nullable=False, server_default=sa.text("1"),
        ),
        sa.Column(
            "unit_price", sa.Numeric(precision=12, scale=2), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_price", sa.Numeric(precision=14, scale=2), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("planned_delivery_date", sa.Date(), nullable=False),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        # 不使用 DB ENUM：varchar(20) + Python PartStatus 校验。
        sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "is_urgent", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
            comment="是否加急",
        ),
        # customer_id 是逻辑外键，指向 t_customer.id（无 DB FK 约束）。
        sa.Column("customer_id", sa.BigInteger, nullable=False),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_part_name", "t_part", ["name"])
    op.create_index("ix_t_part_drawing_no", "t_part", ["drawing_no"])
    op.create_index("ix_t_part_customer_id", "t_part", ["customer_id"])
    op.create_index("ix_t_part_status", "t_part", ["status"])
    op.create_index("ix_t_part_is_urgent", "t_part", ["is_urgent"])
    op.create_index("ix_t_part_request_date", "t_part", ["request_date"])
    op.create_index(
        "ix_t_part_planned_delivery_date", "t_part", ["planned_delivery_date"],
    )
    op.create_index("ix_t_part_deleted_at", "t_part", ["deleted_at"])
    op.create_index(
        "ix_t_part_customer_status_delivery",
        "t_part",
        ["customer_id", "status", "planned_delivery_date"],
    )

    # =================================================================
    # 3) ALTER t_part ADD serial_no（对应原 b2c3d4e5f6a7）
    #    fresh-install 时没有旧数据需要回填，跳过那段 WITH active ... UPDATE
    # =================================================================
    op.add_column(
        "t_part",
        sa.Column("serial_no", sa.String(length=8), nullable=True),
    )
    # 部分唯一索引：仅对非 NULL serial_no，保证全局不重复
    op.create_index(
        "uk_t_part_serial_no",
        "t_part",
        ["serial_no"],
        unique=True,
        postgresql_where=sa.text("serial_no IS NOT NULL"),
    )

    # =================================================================
    # 4) ALTER t_part ADD current_worker_id / released_at（对应原 c3d4e5f6a7b8）
    # =================================================================
    op.add_column(
        "t_part",
        sa.Column("current_worker_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_t_part_current_worker_id", "t_part", ["current_worker_id"],
    )
    op.add_column(
        "t_part",
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_t_part_released_at", "t_part", ["released_at"],
    )

    # =================================================================
    # 5) t_worker：含最终版本的 id_card_no / phone 字段与 uk_t_worker_id_card_no 唯一索引
    #    （合并原 c3d4e5f6a7b8 + d4e5f6a7b8c9 + e5f6a7b8c9d0）
    # =================================================================
    op.create_table(
        "t_worker",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("badge_code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column(
            "id_card_no", sa.String(length=18), nullable=True,
            comment="身份证号；与 id 组成联合唯一索引",
        ),
        sa.Column(
            "phone", sa.String(length=20), nullable=True,
            comment="手机号",
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
            comment="是否在职",
        ),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    # 工牌码唯一索引（未删除行内唯一）
    op.create_index(
        "uk_t_worker_badge_code",
        "t_worker",
        ["badge_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 身份证号唯一索引：仅对非 NULL 约束
    op.create_index(
        "uk_t_worker_id_card_no",
        "t_worker",
        ["id_card_no"],
        unique=True,
        postgresql_where="id_card_no IS NOT NULL",
    )
    op.create_index("ix_t_worker_name", "t_worker", ["name"])
    op.create_index("ix_t_worker_deleted_at", "t_worker", ["deleted_at"])

    # =================================================================
    # 6) t_part_event：订单全生命周期事件流（append-only，EventTimestampMixin）
    # =================================================================
    op.create_table(
        "t_part_event",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("part_id", sa.BigInteger, nullable=False),
        sa.Column("worker_id", sa.BigInteger, nullable=True),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("drawing_code", sa.String(length=100), nullable=True),
        sa.Column("badge_code", sa.String(length=50), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_part_event_part_id", "t_part_event", ["part_id"])
    op.create_index("ix_part_event_created_at", "t_part_event", ["created_at"])
    op.create_index("ix_part_event_event_type", "t_part_event", ["event_type"])
    op.create_index("ix_part_event_worker_id", "t_part_event", ["worker_id"])

    # =================================================================
    # 7) t_serial_counter + 种子 L/F/H counter=0（对应原 f1a2b3c4d5e6）
    # =================================================================
    op.create_table(
        "t_serial_counter",
        sa.Column("prefix", sa.String(length=1), primary_key=True),
        sa.Column(
            "counter", sa.BigInteger, nullable=False,
            server_default=sa.text("0"),
        ),
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
    )
    op.execute(
        sa.text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "VALUES ('L', 0), ('F', 0), ('H', 0) "
            "ON CONFLICT (prefix) DO NOTHING"
        )
    )

    # =================================================================
    # 8) ALTER t_part ADD assembly_id（对应原 1a2b3c4d5e6f）
    # =================================================================
    op.add_column(
        "t_part",
        sa.Column(
            "assembly_id", sa.BigInteger(), nullable=True,
            comment="逻辑外键 → t_assembly.id；NULL = 非装配件子件",
        ),
    )
    op.create_index("ix_t_part_assembly_id", "t_part", ["assembly_id"])
    op.create_index(
        "ix_t_part_assembly_id_status", "t_part", ["assembly_id", "status"],
    )

    # =================================================================
    # 9) t_assembly：4 态 PENDING/IN_PROCESS/COMPLETED/CANCELLED
    # =================================================================
    op.create_table(
        "t_assembly",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column(
            "drawing_no", sa.String(length=100), nullable=False,
            comment="总图图号（如 E42FX1020107101）",
        ),
        sa.Column(
            "name", sa.String(length=200), nullable=False,
            comment="装配体名称（如 精研挡料座）",
        ),
        sa.Column("applicant_name", sa.String(length=50), nullable=True),
        sa.Column(
            "customer_id", sa.BigInteger, nullable=False,
            comment="逻辑外键 → t_customer.id 叶子节点",
        ),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("planned_delivery_date", sa.Date(), nullable=False),
        sa.Column("actual_delivery_date", sa.Date(), nullable=True),
        sa.Column(
            "is_urgent", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default=sa.text("'PENDING'"),
            comment="PENDING（默认）/ IN_PROCESS / COMPLETED / CANCELLED",
        ),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_assembly_drawing_no", "t_assembly", ["drawing_no"])
    op.create_index("ix_t_assembly_customer_id", "t_assembly", ["customer_id"])
    op.create_index("ix_t_assembly_status", "t_assembly", ["status"])
    op.create_index(
        "ix_t_assembly_planned_delivery", "t_assembly", ["planned_delivery_date"],
    )
    op.create_index(
        "ix_t_assembly_customer_status", "t_assembly", ["customer_id", "status"],
    )
    op.create_index("ix_t_assembly_deleted_at", "t_assembly", ["deleted_at"])

    # =================================================================
    # 10) t_drawing_file：COS 上的图纸文件元数据
    # =================================================================
    op.create_table(
        "t_drawing_file",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column(
            "part_id", sa.BigInteger, nullable=True,
            comment="逻辑外键 → t_part.id；NULL 表示挂在装配件上",
        ),
        sa.Column(
            "assembly_id", sa.BigInteger, nullable=True,
            comment="逻辑外键 → t_assembly.id；NULL 表示挂在子件上",
        ),
        sa.Column(
            "file_type", sa.String(length=20), nullable=False,
            comment="PDF / STEP / DWG / DXF",
        ),
        sa.Column(
            "object_key", sa.String(length=500), nullable=False,
            comment="COS 对象 key",
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger, nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column(
            "page_index", sa.Integer(), nullable=True,
            comment="多页 PDF 中该行指向的页码；非 PDF 场景 NULL",
        ),
        sa.Column(
            "upload_status", sa.String(length=20), nullable=False,
            server_default=sa.text("'READY'"),
            comment="PENDING / READY / FAILED",
        ),
        # —— 审计字段 ——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        # part_id 与 assembly_id 二选一非空（XOR）
        sa.CheckConstraint(
            "(part_id IS NOT NULL) <> (assembly_id IS NOT NULL)",
            name="ck_t_drawing_file_owner_xor",
        ),
    )
    op.create_index("ix_t_drawing_file_part_id", "t_drawing_file", ["part_id"])
    op.create_index(
        "ix_t_drawing_file_assembly_id", "t_drawing_file", ["assembly_id"],
    )
    op.create_index(
        "ix_t_drawing_file_part_type", "t_drawing_file", ["part_id", "file_type"],
    )
    op.create_index(
        "ix_t_drawing_file_assembly_type",
        "t_drawing_file", ["assembly_id", "file_type"],
    )
    op.create_index(
        "ix_t_drawing_file_deleted_at", "t_drawing_file", ["deleted_at"],
    )


def downgrade() -> None:
    # 倒序 drop（注意外键关系：先 drop t_drawing_file / t_part_event，
    # 再 drop t_part 加的列/索引，最后 drop t_part / t_customer）
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

    op.drop_index("ix_t_part_assembly_id_status", table_name="t_part")
    op.drop_index("ix_t_part_assembly_id", table_name="t_part")
    op.drop_column("t_part", "assembly_id")

    op.drop_table("t_serial_counter")

    op.drop_index("ix_part_event_worker_id", table_name="t_part_event")
    op.drop_index("ix_part_event_event_type", table_name="t_part_event")
    op.drop_index("ix_part_event_created_at", table_name="t_part_event")
    op.drop_index("ix_part_event_part_id", table_name="t_part_event")
    op.drop_table("t_part_event")

    op.drop_index("ix_t_worker_deleted_at", table_name="t_worker")
    op.drop_index("ix_t_worker_name", table_name="t_worker")
    op.drop_index("uk_t_worker_id_card_no", table_name="t_worker")
    op.drop_index("uk_t_worker_badge_code", table_name="t_worker")
    op.drop_table("t_worker")

    op.drop_index("ix_t_part_released_at", table_name="t_part")
    op.drop_column("t_part", "released_at")
    op.drop_index("ix_t_part_current_worker_id", table_name="t_part")
    op.drop_column("t_part", "current_worker_id")

    op.drop_index("uk_t_part_serial_no", table_name="t_part")
    op.drop_column("t_part", "serial_no")

    op.drop_index(
        "ix_t_part_customer_status_delivery", table_name="t_part",
    )
    op.drop_index("ix_t_part_deleted_at", table_name="t_part")
    op.drop_index("ix_t_part_planned_delivery_date", table_name="t_part")
    op.drop_index("ix_t_part_request_date", table_name="t_part")
    op.drop_index("ix_t_part_is_urgent", table_name="t_part")
    op.drop_index("ix_t_part_status", table_name="t_part")
    op.drop_index("ix_t_part_customer_id", table_name="t_part")
    op.drop_index("ix_t_part_drawing_no", table_name="t_part")
    op.drop_index("ix_t_part_name", table_name="t_part")
    op.drop_table("t_part")

    op.drop_index("ix_t_customer_deleted_at", table_name="t_customer")
    op.drop_constraint(
        "ck_t_customer_no_self_parent", "t_customer", type_="check",
    )
    op.drop_index("ix_t_customer_parent_id", table_name="t_customer")
    op.drop_index("ix_t_customer_name", table_name="t_customer")
    op.drop_table("t_customer")
