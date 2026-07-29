"""part batch split: t_part_batch + t_part_event.batch_id/quantity (2026-07-29)

父子批次方案（部分数量领取 / 送检 / 通过 / 送货）：

- 新建 `t_part_batch`：工单（t_part）的数量载体与状态机载体。
  * 字段与 TPart 的报工字段同名（status / location / current_holder_id /
    next_process_id / placed_at），状态机 PartStateMachine 直接复用。
  * 无 batch_code 列：v1 扫码统一用工单 serial_no，请求体携带 batch_id
    定位批次；展示码 `serial||'B'||batch_no` 在响应层派生，不落库。
  * unique (part_id, batch_no)；状态/holder/location+status+next_process 索引。
- `t_part_event` 新增 `batch_id`（部分索引）与 `quantity` 两列：
  批次级流转事件归属批次并记录本次数量；历史事件 batch_id 回填为根批次。
- 回填：每个 t_part（含软删行）生成 batch_no=1 的根批次，
  quantity / status / location / holder / next_process_id / placed_at /
  delivery_note_id 全部复制，审计时间沿用 part 的 created_at/updated_at；
  软删 part 的根批次同步打 deleted_at。
- 接在 000000000019 之后（master 已占用 019 做 outsource_unify_quote），
  保持单 head 线性拓扑。

注意：若 admin_scan_access 后续占用 000000000020，本文件已 renumber 为 020，
后续合并把 admin_scan_access 改为 021 并把 down_revision 指向 020。

Revision ID: 000000000020
Revises: 000000000019
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from utils.id_gen import new_id


revision: str = "000000000020"
down_revision: Union[str, None] = "000000000019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _audit_cols() -> list[sa.Column]:
    """AuditMixin 列序固定 version → created_at → created_by → updated_at → updated_by → deleted_at。"""
    return [
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default=sa.text("0"),
            comment="乐观锁版本号；每次 UPDATE 自增；冲突抛 BIZ_VERSION_CONFLICT 409",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def upgrade() -> None:
    # ── 1. t_part_batch ──────────────────────────────────────────────
    op.create_table(
        "t_part_batch",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("part_id", sa.BigInteger(), nullable=False, comment="逻辑外键 → t_part.id"),
        sa.Column("batch_no", sa.Integer(), nullable=False, comment="工单内批次序号（1 起）"),
        sa.Column("quantity", sa.Integer(), nullable=False, comment="本批次数量"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column(
            "location", sa.String(length=20), nullable=True,
            comment="OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF / OUTSOURCE_COMPANY",
        ),
        sa.Column("current_holder_id", sa.BigInteger(), nullable=True, comment="多态 holder：shelf/worker/outsource_company"),
        sa.Column("next_process_id", sa.BigInteger(), nullable=True, comment="逻辑外键 → t_process.id"),
        sa.Column("placed_at", sa.DateTime(), nullable=True, comment="批次首次进入 ON_SHELF 的时间"),
        sa.Column("delivery_note_id", sa.BigInteger(), nullable=True, comment="逻辑外键 → t_delivery_note.id"),
        sa.Column("parent_batch_id", sa.BigInteger(), nullable=True, comment="拆分谱系：源批次 id；根批次 NULL"),
        *_audit_cols(),
        sa.UniqueConstraint("part_id", "batch_no", name="uq_t_part_batch_part_no"),
    )
    op.create_index("ix_t_part_batch_part_id", "t_part_batch", ["part_id"])
    op.create_index("ix_t_part_batch_status", "t_part_batch", ["status"])
    op.create_index("ix_t_part_batch_location", "t_part_batch", ["location"])
    op.create_index("ix_t_part_batch_current_holder_id", "t_part_batch", ["current_holder_id"])
    op.create_index("ix_t_part_batch_next_process_id", "t_part_batch", ["next_process_id"])
    op.create_index("ix_t_part_batch_placed_at", "t_part_batch", ["placed_at"])
    op.create_index("ix_t_part_batch_delivery_note_id", "t_part_batch", ["delivery_note_id"])
    op.create_index("ix_t_part_batch_deleted_at", "t_part_batch", ["deleted_at"])
    op.create_index(
        "ix_t_part_batch_status_holder", "t_part_batch",
        ["status", "current_holder_id"],
    )
    op.create_index(
        "ix_t_part_batch_location_status_next_process", "t_part_batch",
        ["location", "status", "next_process_id"],
    )

    # ── 2. t_part_event 加列 ─────────────────────────────────────────
    op.add_column(
        "t_part_event",
        sa.Column(
            "batch_id", sa.BigInteger(), nullable=True,
            comment="逻辑外键 → t_part_batch.id；NULL = 工单级事件",
        ),
    )
    op.add_column(
        "t_part_event",
        sa.Column(
            "quantity", sa.Integer(), nullable=True,
            comment="本次事件涉及的数量；NULL = 历史数据 / 不适用",
        ),
    )
    op.create_index(
        "ix_t_part_event_batch_id", "t_part_event", ["batch_id"],
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )

    # ── 3. 回填：每个 part 生成根批次 + 事件挂根批次 ─────────────────
    bind = op.get_bind()
    parts = bind.execute(
        sa.text(
            "SELECT id, quantity, status, location, current_holder_id,"
            "       next_process_id, placed_at, delivery_note_id,"
            "       created_at, created_by, updated_at, updated_by, deleted_at"
            " FROM t_part"
        )
    ).mappings()
    insert_batch = sa.text(
        "INSERT INTO t_part_batch ("
        "  id, part_id, batch_no, quantity, status, location,"
        "  current_holder_id, next_process_id, placed_at, delivery_note_id,"
        "  parent_batch_id, version,"
        "  created_at, created_by, updated_at, updated_by, deleted_at"
        ") VALUES ("
        "  :id, :part_id, 1, :quantity, :status, :location,"
        "  :current_holder_id, :next_process_id, :placed_at, :delivery_note_id,"
        "  NULL, 0,"
        "  :created_at, :created_by, :updated_at, :updated_by, :deleted_at"
        ")"
    )
    attach_events = sa.text(
        "UPDATE t_part_event SET batch_id = :batch_id WHERE part_id = :part_id"
    )
    for p in parts:
        batch_id = new_id()
        bind.execute(insert_batch, {**p, "id": batch_id, "part_id": p["id"]})
        bind.execute(attach_events, {"batch_id": batch_id, "part_id": p["id"]})


def downgrade() -> None:
    op.drop_index("ix_t_part_event_batch_id", table_name="t_part_event")
    op.drop_column("t_part_event", "quantity")
    op.drop_column("t_part_event", "batch_id")

    op.drop_table("t_part_batch")
