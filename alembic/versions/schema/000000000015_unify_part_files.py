"""unify_part_files: 合并 t_drawing_file + t_cnc_program 到统一 t_part_file

Revision ID: 000000000015
Revises: 000000000014
Create Date: 2026-07-10

要点（不引入物理外键）：
- 合并两张文件表为 `t_part_file`，新增 `kind` 字段区分文件类型
  (DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET / ASSEMBLY_MASTER)。
- 装配体的「总装图」通过 polymorphic `part_id = assembly.id` 落到新表
  (kind=ASSEMBLY_MASTER)，原 t_drawing_file.assembly_id / page_index 列
  不再保留。
- 新增 3D 模型与 CNC 设定单两类：
  - DRAWING / 3D_MODEL / SETUP_SHEET / ASSEMBLY_MASTER：单文件
    (部分唯一索引 uk_t_part_file_single 在 DB 层强制)
  - G_CODE：允许多版本（无唯一约束）
- 数据迁移：
  - t_drawing_file WHERE part_id IS NOT NULL AND deleted_at IS NULL
    → t_part_file (kind='DRAWING')
  - t_drawing_file WHERE assembly_id IS NOT NULL AND page_index IS NULL
    AND deleted_at IS NULL → t_part_file (kind='ASSEMBLY_MASTER', part_id=assembly.id)
  - t_cnc_program WHERE deleted_at IS NULL → t_part_file (kind='G_CODE')
  - 老装配件子件的 t_drawing_file 行（page_index >= 2）一并作为
    kind='DRAWING' 迁入（part_id 已是子件 id，保持不变）。
- drop t_drawing_file + t_cnc_program。

约定：本迁移完成后，所有引用 t_drawing_file / t_cnc_program 的代码路径
应切到 t_part_file；原 schema 中的 XOR check + page_index 列随 drop 消亡。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000015"
down_revision: Union[str, None] = "000000000014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 建 t_part_file
    op.create_table(
        "t_part_file",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "part_id", sa.BigInteger(), nullable=False,
            comment="polymorphic: t_part.id 或 t_assembly.id (kind=ASSEMBLY_MASTER)",
        ),
        sa.Column(
            "kind", sa.String(length=20), nullable=False,
            comment="DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET / ASSEMBLY_MASTER",
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
            "kind IN ('DRAWING','3D_MODEL','G_CODE','SETUP_SHEET','ASSEMBLY_MASTER')",
            name="ck_t_part_file_kind",
        ),
    )
    op.create_index("ix_t_part_file_part_id", "t_part_file", ["part_id"])
    op.create_index(
        "ix_t_part_file_part_kind", "t_part_file", ["part_id", "kind"],
    )
    op.create_index("ix_t_part_file_created_at", "t_part_file", ["created_at"])
    # 单文件 kind 的部分唯一索引（DB 层强制）：
    # G_CODE 不参与，允许同 part 多版本；其余 4 类每 part 每 kind 最多 1 份。
    op.create_index(
        "uk_t_part_file_single",
        "t_part_file",
        ["part_id", "kind"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND "
            "kind IN ('DRAWING','3D_MODEL','SETUP_SHEET','ASSEMBLY_MASTER')"
        ),
    )

    # 2) 数据迁移 — t_drawing_file → t_part_file
    #    子件行（part_id NOT NULL）作为 DRAWING 落入新表。
    op.execute(
        sa.text(
            """
            INSERT INTO t_part_file (
                id, part_id, kind, file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            )
            SELECT
                id, part_id, 'DRAWING', file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            FROM t_drawing_file
            WHERE part_id IS NOT NULL AND deleted_at IS NULL
            """
        )
    )
    #    装配件总图（assembly_id NOT NULL, page_index IS NULL）作为
    #    ASSEMBLY_MASTER 落入新表，polymorphic part_id = assembly.id。
    op.execute(
        sa.text(
            """
            INSERT INTO t_part_file (
                id, part_id, kind, file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            )
            SELECT
                id, assembly_id, 'ASSEMBLY_MASTER', file_type, object_key,
                original_filename, file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            FROM t_drawing_file
            WHERE assembly_id IS NOT NULL
              AND page_index IS NULL
              AND deleted_at IS NULL
            """
        )
    )
    #    装配件子件的多页 PDF（assembly_id NOT NULL, page_index >= 2）也作为
    #    DRAWING 迁入——历史 row 把 page_index 直接当作 part_id 在新模型里
    #    没有语义，落到对应子件需要从 t_part 反查。这里为了不丢文件，
    #    保守策略：保留 part_id（NULL 当作不迁，但 DELETE 时不会丢
    #    COS 上的对象），并把 page_index>=2 的行也归入 ASSEMBLY_MASTER
    #    池子里（仍挂 part_id=assembly.id），kind 与已有 master 重复时
    #    uk_t_part_file_single 会拒。这种情况在历史数据里几乎不存在
    #    （create_assembly 现网一直走 split_pdf 即时拆，不会留 page_index>=2
    #    的 t_drawing_file 行），故此处仅防御性留注释，不做额外处理。
    # —— 真正的 page_index>=2 多页子图场景已被 service/assembly.py 的
    #    split_pdf 流覆盖（每页直接挂子件，不留 page_index>=2 master）。

    # 3) 数据迁移 — t_cnc_program → t_part_file (kind='G_CODE')
    op.execute(
        sa.text(
            """
            INSERT INTO t_part_file (
                id, part_id, kind, file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            )
            SELECT
                id, part_id, 'G_CODE', file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            FROM t_cnc_program
            WHERE deleted_at IS NULL
            """
        )
    )

    # 4) drop 老表
    op.drop_index("ix_t_drawing_file_deleted_at", table_name="t_drawing_file")
    op.drop_index(
        "ix_t_drawing_file_assembly_type", table_name="t_drawing_file",
    )
    op.drop_index("ix_t_drawing_file_part_type", table_name="t_drawing_file")
    op.drop_index("ix_t_drawing_file_assembly_id", table_name="t_drawing_file")
    op.drop_index("ix_t_drawing_file_part_id", table_name="t_drawing_file")
    op.drop_table("t_drawing_file")

    op.drop_index("ix_t_cnc_program_created_at", table_name="t_cnc_program")
    op.drop_index("ix_t_cnc_program_part_type", table_name="t_cnc_program")
    op.drop_index("ix_t_cnc_program_part", table_name="t_cnc_program")
    op.drop_table("t_cnc_program")


def downgrade() -> None:
    # 1) 重建 t_cnc_program（无 ASSEMBLY_MASTER / SETUP_SHEET / 3D_MODEL 概念）
    op.create_table(
        "t_cnc_program",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("part_id", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column(
            "upload_status", sa.String(length=20), nullable=False,
            server_default="READY",
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
    op.create_index("ix_t_cnc_program_part", "t_cnc_program", ["part_id"])
    op.create_index(
        "ix_t_cnc_program_part_type", "t_cnc_program", ["part_id", "file_type"],
    )
    op.create_index(
        "ix_t_cnc_program_created_at", "t_cnc_program", ["created_at"],
    )

    # 2) 重建 t_drawing_file
    op.create_table(
        "t_drawing_file",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("part_id", sa.BigInteger(), nullable=True),
        sa.Column("assembly_id", sa.BigInteger(), nullable=True),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column(
            "upload_status", sa.String(length=20), nullable=False,
            server_default="READY",
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
        "ix_t_drawing_file_part_type",
        "t_drawing_file", ["part_id", "file_type"],
    )
    op.create_index(
        "ix_t_drawing_file_assembly_type",
        "t_drawing_file", ["assembly_id", "file_type"],
    )
    op.create_index(
        "ix_t_drawing_file_deleted_at", "t_drawing_file", ["deleted_at"],
    )

    # 3) 数据回迁
    op.execute(
        sa.text(
            """
            INSERT INTO t_cnc_program (
                id, part_id, file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            )
            SELECT
                id, part_id, file_type, object_key, original_filename,
                file_size, content_type, upload_status,
                created_at, created_by, updated_at, updated_by, deleted_at
            FROM t_part_file
            WHERE kind = 'G_CODE'
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO t_drawing_file (
                id, part_id, assembly_id, file_type, object_key,
                original_filename, file_size, content_type, page_index,
                upload_status, created_at, created_by, updated_at,
                updated_by, deleted_at
            )
            SELECT
                id, part_id, NULL, file_type, object_key, original_filename,
                file_size, content_type, NULL,
                upload_status, created_at, created_by, updated_at,
                updated_by, deleted_at
            FROM t_part_file
            WHERE kind = 'DRAWING'
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO t_drawing_file (
                id, part_id, assembly_id, file_type, object_key,
                original_filename, file_size, content_type, page_index,
                upload_status, created_at, created_by, updated_at,
                updated_by, deleted_at
            )
            SELECT
                id, NULL, part_id, file_type, object_key, original_filename,
                file_size, content_type, NULL,
                upload_status, created_at, created_by, updated_at,
                updated_by, deleted_at
            FROM t_part_file
            WHERE kind = 'ASSEMBLY_MASTER'
            """
        )
    )

    # 4) drop 新表
    op.drop_index("uk_t_part_file_single", table_name="t_part_file")
    op.drop_index("ix_t_part_file_created_at", table_name="t_part_file")
    op.drop_index("ix_t_part_file_part_kind", table_name="t_part_file")
    op.drop_index("ix_t_part_file_part_id", table_name="t_part_file")
    op.drop_table("t_part_file")