"""schema_init: 一次性建全部表/索引/约束（squash 合并）

Revision ID: 000000000001
Revises:
Create Date: 2026-07-10

说明：
- 本文件是 myERP 的**唯一 schema 迁移**，把历史上分散的所有 DDL 迁移
  （init_schema / cnc_program / applicant / shelf_process / assembly_serial_no /
  customer_serial_prefix / unify_part_files / shelf_display_order /
  user_refresh_token_version / part_event_operator / part_file_sha_and_kinds）
  合并为一份「最终 schema」。
- 2026-07-14 重新 squash：把 14b0e5f 之后的 003 (event_operator) + 004
  (file_sha+kinds) 合并进来；data_init (002) 已含新菜单映射。
- **fresh-install only**：假定数据库为空。
- **不使用物理外键**（CLAUDE.md §1）：所有跨表引用都是普通列 + 普通索引。
- **不使用 DB ENUM**：status 等一律 varchar，合法性由 Python Enum 在 service 层校验。
- 审计字段列顺序与 model/audit.py:AuditMixin 严格对齐。
- t_customer.id 用 autoincrement=False（不建 sequence）：全表统一雪花 ID，
  App 侧 default=new_id 显式传入。
- 文件表已统一为 t_part_file（polymorphic kind），不再有 t_drawing_file /
  t_cnc_program。

表清单（共 17 张）：
  t_customer / t_part / t_worker / t_work_type_process / t_process / t_work_type /
  t_part_event / t_serial_counter / t_assembly / t_part_file / t_user /
  t_user_role / t_shelf / t_menu / t_role_menu / t_applicant / t_shelf_process
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
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(length=100), nullable=False),
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
        sa.Column(
            "serial_prefix", sa.String(length=1), nullable=True,
            comment="一级客户序列号前缀（A-Z）；叶子客户 NULL",
        ),
    )
    op.create_index("ix_t_customer_name", "t_customer", ["name"])
    op.create_index("ix_t_customer_parent_id", "t_customer", ["parent_id"])
    op.create_index("ix_t_customer_deleted_at", "t_customer", ["deleted_at"])
    op.create_check_constraint(
        "ck_t_customer_no_self_parent",
        "t_customer",
        "parent_id IS NULL OR parent_id <> id",
    )
    op.create_check_constraint(
        "ck_t_customer_serial_prefix_uppercase",
        "t_customer",
        "serial_prefix IS NULL OR serial_prefix ~ '^[A-Z]$'",
    )
    op.create_index(
        "uq_t_customer_root_prefix",
        "t_customer",
        ["serial_prefix"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND parent_id IS NULL AND serial_prefix IS NOT NULL"
        ),
    )

    # =================================================================
    # 2) t_part：零件订单
    # =================================================================
    op.create_table(
        "t_part",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("serial_no", sa.String(length=8), nullable=True),
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
        sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "location", sa.String(length=20), nullable=True,
            comment="零件物理位置: OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF",
        ),
        sa.Column(
            "is_urgent", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
            comment="是否加急",
        ),
        sa.Column(
            "current_holder_id", sa.BigInteger(), nullable=True,
            comment="多态 holder → t_worker.id 或 t_shelf.id",
        ),
        sa.Column(
            "placed_at", sa.DateTime(), nullable=True,
            comment="PENDING→IN_PROCESS 时置位",
        ),
        sa.Column(
            "next_process_id", sa.BigInteger(), nullable=True,
            comment="逻辑外键 → t_process.id；place_on_shelf / RETURNED 时更新",
        ),
        sa.Column("customer_id", sa.BigInteger, nullable=False),
        sa.Column(
            "assembly_id", sa.BigInteger(), nullable=True,
            comment="逻辑外键 → t_assembly.id；NULL = 非装配件子件",
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
        "uk_t_part_serial_no",
        "t_part",
        ["serial_no"],
        unique=True,
        postgresql_where=sa.text("serial_no IS NOT NULL"),
    )
    op.create_index(
        "ix_t_part_current_holder_id", "t_part", ["current_holder_id"],
    )
    op.create_index("ix_t_part_placed_at", "t_part", ["placed_at"])
    op.create_index("ix_t_part_location", "t_part", ["location"])
    op.create_index("ix_t_part_assembly_id", "t_part", ["assembly_id"])
    op.create_index(
        "ix_t_part_customer_status_delivery",
        "t_part",
        ["customer_id", "status", "planned_delivery_date"],
    )
    op.create_index(
        "ix_t_part_assembly_id_status", "t_part", ["assembly_id", "status"],
    )
    op.create_index(
        "ix_t_part_status_holder",
        "t_part",
        ["status", "current_holder_id"],
    )
    op.create_index(
        "ix_t_part_location_status_next_process",
        "t_part",
        ["location", "status", "next_process_id"],
    )
    op.create_index(
        "ix_t_part_next_process_id", "t_part", ["next_process_id"],
    )

    # =================================================================
    # 3) t_worker
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
        sa.Column(
            "work_type_id", sa.BigInteger(), nullable=True,
            comment="逻辑外键 → t_work_type.id；NULL = 未分配工种",
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
    op.create_index(
        "uk_t_worker_badge_code",
        "t_worker",
        ["badge_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uk_t_worker_id_card_no",
        "t_worker",
        ["id_card_no"],
        unique=True,
        postgresql_where="id_card_no IS NOT NULL",
    )
    op.create_index("ix_t_worker_name", "t_worker", ["name"])
    op.create_index("ix_t_worker_deleted_at", "t_worker", ["deleted_at"])
    op.create_index("ix_t_worker_work_type_id", "t_worker", ["work_type_id"])

    # =================================================================
    # 4) t_work_type_process：工种 ↔ 工序 N:M（子表先建）
    # =================================================================
    op.create_table(
        "t_work_type_process",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("work_type_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_work_type.id"),
        sa.Column("process_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_process.id"),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
            comment="工序在工种映射内的显示顺序",
        ),
        # —— 审计字段 ——
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "work_type_id <> process_id",
            name="ck_t_work_type_process_no_self_loop",
        ),
    )
    op.create_index(
        "uk_t_work_type_process",
        "t_work_type_process",
        ["work_type_id", "process_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_t_work_type_process_work_type", "t_work_type_process", ["work_type_id"],
    )
    op.create_index(
        "ix_t_work_type_process_process", "t_work_type_process", ["process_id"],
    )
    op.create_index(
        "ix_t_work_type_process_deleted_at", "t_work_type_process", ["deleted_at"],
    )

    # =================================================================
    # 5) t_process：工序
    # =================================================================
    op.create_table(
        "t_process",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False,
                  comment="工序代码（业务唯一键，不可变）"),
        sa.Column("name", sa.String(length=50), nullable=False,
                  comment="工序名称（前端显示）"),
        sa.Column("category", sa.String(length=16), nullable=False,
                  comment="INHOUSE 自产 / OUTSOURCE 外协"),
        sa.Column(
            "is_inspection", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
            comment="是否品检工序",
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
            comment="显示顺序",
        ),
        sa.Column("description", sa.String(length=200), nullable=True),
        # —— 审计字段 ——
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "category IN ('INHOUSE', 'OUTSOURCE')",
            name="ck_t_process_category",
        ),
    )
    op.create_index(
        "uk_t_process_code",
        "t_process",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_process_code", "t_process", ["code"])
    op.create_index("ix_t_process_category", "t_process", ["category"])
    op.create_index("ix_t_process_deleted_at", "t_process", ["deleted_at"])

    # =================================================================
    # 6) t_work_type：工种
    # =================================================================
    op.create_table(
        "t_work_type",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False,
                  comment="工种代码（业务唯一键，不可变）"),
        sa.Column("name", sa.String(length=50), nullable=False,
                  comment="工种名称（前端显示）"),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
            comment="显示顺序",
        ),
        # —— 审计字段 ——
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uk_t_work_type_code",
        "t_work_type",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_work_type_code", "t_work_type", ["code"])
    op.create_index("ix_t_work_type_deleted_at", "t_work_type", ["deleted_at"])

    # =================================================================
    # 7) t_part_event：订单全生命周期事件流（append-only）
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
        sa.Column(
            "created_by", sa.BigInteger, nullable=True,
            comment="操作者 t_user.id（NULL = 系统调度/历史数据）",
        ),
    )
    op.create_index("ix_part_event_part_id", "t_part_event", ["part_id"])
    op.create_index("ix_part_event_created_at", "t_part_event", ["created_at"])
    op.create_index("ix_part_event_event_type", "t_part_event", ["event_type"])
    op.create_index("ix_part_event_worker_id", "t_part_event", ["worker_id"])

    # =================================================================
    # 8) t_serial_counter：流水号计数器
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
        sa.Column(
            "serial_no", sa.String(length=8), nullable=True,
            comment="装配体序列号；子件派生为 '{serial_no}-{i:02d}'",
        ),
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
    op.create_index("ix_t_assembly_serial_no", "t_assembly", ["serial_no"])
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uk_t_assembly_serial_no "
            "ON t_assembly (serial_no) "
            "WHERE deleted_at IS NULL AND serial_no IS NOT NULL"
        )
    )

    # =================================================================
    # 10) t_part_file：统一文件表（polymorphic kind；2026-07-14 加 content_sha256
    #     + kind CAD_2D）
    # =================================================================
    op.create_table(
        "t_part_file",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "part_id", sa.BigInteger(), nullable=False,
            comment="polymorphic: t_part.id 或 t_assembly.id (kind=ASSEMBLY_MASTER)",
        ),
        sa.Column(
            "kind", sa.String(length=20), nullable=False,
            comment="DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET / ASSEMBLY_MASTER / CAD_2D",
        ),
        sa.Column(
            "file_type", sa.String(length=20), nullable=False,
            comment="扩展名大写（PDF / STEP / NC / ...），与 kind 配套",
        ),
        sa.Column(
            "object_key", sa.String(length=500), nullable=False,
            comment="COS 对象 key",
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column(
            "upload_status", sa.String(length=20), nullable=False,
            server_default="READY",
            comment="PENDING / READY / FAILED",
        ),
        # 2026-07-14：内容去重（同一 part_id + kind + sha 仅 1 行活跃）
        sa.Column(
            "content_sha256", sa.CHAR(length=64), nullable=True,
            comment="SHA-256 hex of file bytes（去重用）；NULL = 未计算 / 历史记录",
        ),
        # AuditMixin
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
            "kind IN ('DRAWING','3D_MODEL','G_CODE','SETUP_SHEET',"
            "'ASSEMBLY_MASTER','CAD_2D')",
            name="ck_t_part_file_kind",
        ),
    )
    op.create_index("ix_t_part_file_part_id", "t_part_file", ["part_id"])
    op.create_index(
        "ix_t_part_file_part_kind", "t_part_file", ["part_id", "kind"],
    )
    op.create_index("ix_t_part_file_created_at", "t_part_file", ["created_at"])
    op.create_index(
        "uk_t_part_file_single",
        "t_part_file",
        ["part_id", "kind"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND "
            "kind IN ('DRAWING','3D_MODEL','SETUP_SHEET',"
            "'ASSEMBLY_MASTER','CAD_2D')"
        ),
    )
    # 2026-07-14：内容去重部分唯一索引
    #   (part_id, kind, content_sha256) WHERE deleted_at IS NULL AND sha NOT NULL
    op.create_index(
        "uk_t_part_file_part_kind_sha",
        "t_part_file",
        ["part_id", "kind", "content_sha256"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND content_sha256 IS NOT NULL"
        ),
    )

    # =================================================================
    # 11) t_user：账号主表
    # =================================================================
    op.create_table(
        "t_user",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=50), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        # —— 审计字段 ——
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
        sa.Column(
            "refresh_token_version", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
            comment="refresh token 轮转计数器；每次成功 refresh 后 +1",
        ),
    )
    op.create_index(
        "uk_t_user_username",
        "t_user",
        ["username"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_user_deleted_at", "t_user", ["deleted_at"])

    # =================================================================
    # 12) t_user_role：账号 ↔ 角色多对多
    # =================================================================
    op.create_table(
        "t_user_role",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_id", sa.BigInteger(), nullable=True),
        # —— 审计字段 ——
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
        sa.UniqueConstraint(
            "user_id", "role", "scope_type", "scope_id",
            name="uk_t_user_role_user_role_scope",
        ),
    )
    op.create_index(
        "ix_t_user_role_user_id", "t_user_role", ["user_id"],
    )
    op.create_index(
        "ix_t_user_role_scope", "t_user_role", ["scope_type", "scope_id"],
    )
    op.create_index("ix_t_user_role_deleted_at", "t_user_role", ["deleted_at"])

    # =================================================================
    # 13) t_shelf：货架实体
    # =================================================================
    op.create_table(
        "t_shelf",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("zone", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        # —— 审计字段 ——
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
        sa.Column(
            "display_order", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
            comment="物理顺序（0=未设置；manager 在 ShelfList 后台手填）",
        ),
    )
    op.create_index(
        "uk_t_shelf_code",
        "t_shelf",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_shelf_zone", "t_shelf", ["zone"])
    op.create_index("ix_t_shelf_deleted_at", "t_shelf", ["deleted_at"])
    op.create_index(
        "ix_t_shelf_display_order",
        "t_shelf",
        ["display_order", "code"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # =================================================================
    # 14) t_menu：菜单主表（邻接表）
    # =================================================================
    op.create_table(
        "t_menu",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=50), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=True),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        # —— 审计字段 ——
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
            "parent_id IS NULL OR parent_id <> id",
            name="ck_t_menu_no_self_loop",
        ),
    )
    op.create_index(
        "uk_t_menu_code",
        "t_menu",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_menu_parent_id", "t_menu", ["parent_id"])
    op.create_index("ix_t_menu_deleted_at", "t_menu", ["deleted_at"])

    # =================================================================
    # 15) t_role_menu：角色↔菜单 N:M
    # =================================================================
    op.create_table(
        "t_role_menu",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("menu_id", sa.BigInteger(), nullable=False),
        # —— 审计字段 ——
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
    op.create_index(
        "uk_t_role_menu_role_menu",
        "t_role_menu",
        ["role", "menu_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_role_menu_role", "t_role_menu", ["role"])
    op.create_index("ix_t_role_menu_menu_id", "t_role_menu", ["menu_id"])
    op.create_index("ix_t_role_menu_deleted_at", "t_role_menu", ["deleted_at"])

    # =================================================================
    # 16) t_applicant：申请人（姓名 + 所属一级客户，多对一）
    # =================================================================
    op.create_table(
        "t_applicant",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False,
                  comment="申请人姓名"),
        sa.Column("customer_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_customer.id（一级客户）"),
        # AuditMixin
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_applicant_customer_id", "t_applicant", ["customer_id"])
    op.create_index("ix_t_applicant_name", "t_applicant", ["name"])
    op.create_index("ix_t_applicant_deleted_at", "t_applicant", ["deleted_at"])
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_t_applicant_name_customer_active
            ON t_applicant (name, customer_id)
            WHERE deleted_at IS NULL
            """
        )
    )

    # =================================================================
    # 17) t_shelf_process：货架 ↔ 工序 N:M
    # =================================================================
    op.create_table(
        "t_shelf_process",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("shelf_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_shelf.id"),
        sa.Column("process_id", sa.BigInteger(), nullable=False,
                  comment="逻辑外键 → t_process.id"),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default=sa.text("0"),
                  comment="工序在货架映射内的显示顺序"),
        # AuditMixin
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_t_shelf_process_shelf", "t_shelf_process", ["shelf_id"])
    op.create_index("ix_t_shelf_process_process", "t_shelf_process", ["process_id"])
    op.create_index("ix_t_shelf_process_deleted_at", "t_shelf_process", ["deleted_at"])
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uk_t_shelf_process
            ON t_shelf_process (shelf_id, process_id)
            WHERE deleted_at IS NULL
            """
        )
    )
    op.create_check_constraint(
        "ck_t_shelf_process_no_self_loop",
        "t_shelf_process",
        "shelf_id <> process_id",
    )


def downgrade() -> None:
    # 无物理外键，drop 顺序无所谓；drop_table 自动清掉自身索引/约束。
    for table in (
        "t_shelf_process",
        "t_applicant",
        "t_role_menu",
        "t_menu",
        "t_shelf",
        "t_user_role",
        "t_user",
        "t_part_file",
        "t_assembly",
        "t_serial_counter",
        "t_part_event",
        "t_work_type",
        "t_process",
        "t_work_type_process",
        "t_worker",
        "t_part",
        "t_customer",
    ):
        op.drop_table(table)
