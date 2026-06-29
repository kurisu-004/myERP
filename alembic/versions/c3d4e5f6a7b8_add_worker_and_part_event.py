"""add worker, part_event; convert status to varchar; add worker/released cols

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-28

要点（务必遵守）：
1. **不使用物理外键**：所有跨表引用都是普通列 + 普通索引；
   引用完整性由 service 层校验。
2. **不使用 DB 枚举字段**（CLAUDE.md 待补 §9）：把上一版迁移里
   `t_part.status` 的 PostgreSQL `part_status` enum 列迁成 `varchar(20)`，
   状态取值由 Python `PartStatus` Enum 在 service 层校验。
3. `t_part_event` 是订单全生命周期事件流（CREATED / RELEASED /
   PICKED_UP / RETURNED / INSPECTED / STATUS_CHANGED 等），不只是扫码事件。
4. `t_part_event` 故意不继承 `Base`：事件型只追加，无 updated_at / deleted_at。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================================
    # 1) 把 t_part.status 从 PostgreSQL enum 改成 varchar(20)
    #    （只有当 part_status 类型还存在时才执行，兼容 fresh install）
    # ============================================================
    conn = op.get_bind()
    has_enum = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'part_status'")
    ).scalar()
    if has_enum:
        # 强制把 enum 列转 varchar：先 DROP DEFAULT 解耦，ALTER TYPE 改类型，
        # 再 SET DEFAULT 回字面量，最后 DROP TYPE。
        op.execute("ALTER TABLE t_part ALTER COLUMN status DROP DEFAULT")
        op.execute(
            "ALTER TABLE t_part ALTER COLUMN status TYPE varchar(20) "
            "USING status::text"
        )
        op.execute(
            "ALTER TABLE t_part ALTER COLUMN status SET DEFAULT 'PENDING'"
        )
        # 在 alembic 单事务里需要先 COMMIT 再 DROP TYPE（CASCADE 兜底，
        # 避免因列默认值缓存仍指向 enum 而失败）
        op.execute("COMMIT")
        op.execute("DROP TYPE IF EXISTS part_status CASCADE")

    # ============================================================
    # 2) t_part 加两列
    # ============================================================
    # current_worker_id：IN_PROCESS 时谁持有这个零件；逻辑外键 → t_worker.id
    op.add_column(
        "t_part",
        sa.Column("current_worker_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_t_part_current_worker_id",
        "t_part",
        ["current_worker_id"],
    )
    # released_at：文员点击"开始生产"的时间（PENDING → READY 时置位）
    op.add_column(
        "t_part",
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_t_part_released_at",
        "t_part",
        ["released_at"],
    )

    # ============================================================
    # 3) 新增 t_worker
    # ============================================================
    op.create_table(
        "t_worker",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("badge_code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="是否在职",
        ),
        # —— 审计字段 ——
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
    # 工牌码唯一索引（未删除行内唯一）
    op.create_index(
        "uk_t_worker_badge_code",
        "t_worker",
        ["badge_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_t_worker_name", "t_worker", ["name"])
    op.create_index("ix_t_worker_deleted_at", "t_worker", ["deleted_at"])

    # ============================================================
    # 4) 新增 t_part_event（订单全生命周期事件流）
    # ============================================================
    # 所有列都是普通类型：DB 不存 ENUM，全部由 Python Enum 在 service 层校验。
    op.create_table(
        "t_part_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("part_id", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("drawing_code", sa.String(length=100), nullable=True),
        sa.Column("badge_code", sa.String(length=50), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_part_event_part_id", "t_part_event", ["part_id"])
    op.create_index("ix_part_event_created_at", "t_part_event", ["created_at"])
    op.create_index(
        "ix_part_event_event_type", "t_part_event", ["event_type"]
    )
    op.create_index(
        "ix_part_event_worker_id", "t_part_event", ["worker_id"]
    )


def downgrade() -> None:
    # 4) 删 t_part_event
    op.drop_index("ix_part_event_worker_id", table_name="t_part_event")
    op.drop_index("ix_part_event_event_type", table_name="t_part_event")
    op.drop_index("ix_part_event_created_at", table_name="t_part_event")
    op.drop_index("ix_part_event_part_id", table_name="t_part_event")
    op.drop_table("t_part_event")

    # 3) 删 t_worker
    op.drop_index("ix_t_worker_deleted_at", table_name="t_worker")
    op.drop_index("ix_t_worker_name", table_name="t_worker")
    op.drop_index("uk_t_worker_badge_code", table_name="t_worker")
    op.drop_table("t_worker")

    # 2) 删 t_part 新加的列
    op.drop_index("ix_t_part_released_at", table_name="t_part")
    op.drop_column("t_part", "released_at")
    op.drop_index("ix_t_part_current_worker_id", table_name="t_part")
    op.drop_column("t_part", "current_worker_id")

    # 1) 把 t_part.status 还原回 PostgreSQL enum
    op.execute(
        """
        CREATE TYPE part_status AS ENUM (
            'PENDING', 'IN_PROCESS', 'INSPECTION',
            'READY_TO_SHIP', 'DELIVERED',
            'REPAIRING', 'COMPLETED', 'CANCELLED'
        )
        """
    )
    op.execute(
        "ALTER TABLE t_part ALTER COLUMN status TYPE part_status "
        "USING status::part_status"
    )