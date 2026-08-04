"""t_work_type.max_held_batches + t_pickup_skip_event (2026-08-05)

PR-K 系列：六合一功能批次 — 任务 4（跳序取件记录）+ 任务 5（工种持有上限）。

修改表：
- t_work_type：加列 `max_held_batches` Integer NULL（NULL=不限）。
  * service/part.py::pick_up_by_scan 在 worker active 校验后据此拦截。
  * 错误码 BIZ_WORKER_HOLD_LIMIT_EXCEEDED = 20204。

新表：
- t_pickup_skip_event（append-only，无 version / updated_at / deleted_at）：
  * 工人跳过更早 planned_delivery_date 候选件的领取记录（事件表）。
  * 加急件（is_urgent=TRUE）永不记录。
  * 含快照字段（part_serial_no / batch_no）防止流水号被释放复用后失真。
  * 字段含义详见 docstring 行内 comment。

接在 `000000000028`（add_part_has_been_repaired）之后；新 prod_data 迁移
`000000000030` 接在本迁移之后。

Revision ID: 000000000029
Revises: 000000000028
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "000000000029"
# 单 head 线性拓扑：… 027 → 028 → 029（工种上限 + 跳序事件）
down_revision: Union[str, None] = "000000000028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================================
    # 1) t_work_type：加 max_held_batches 列（工种工人持有批次数上限）
    #    NULL = 不限；≥1 = 工人工种持有 active 批次（status=IN_PROCESS +
    #    location=WORKER + holder=worker_id）达到上限禁止再领。
    #    不加索引（仅工种列表/编辑页展示 + 领取校验读使用，量级小）。
    # ============================================================
    op.add_column(
        "t_work_type",
        sa.Column(
            "max_held_batches",
            sa.Integer(),
            nullable=True,
            comment="工种工人最多可同时持有批次数；NULL=不限",
        ),
    )

    # ============================================================
    # 2) t_pickup_skip_event：跳序取件事件流（append-only，无 OCC）
    #    仅 created_at；不再加 version/updated_at/deleted_at。
    #    字段顺序：id → 业务主键 → 快照 → 时间。
    # ============================================================
    op.create_table(
        "t_pickup_skip_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "worker_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_worker.id；触发跳序的工人",
        ),
        sa.Column(
            "part_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_part.id；本次实际领取的工单",
        ),
        sa.Column(
            "batch_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_part_batch.id；记录实际领取批次（拆分后为新批次）",
        ),
        sa.Column(
            "batch_no", sa.Integer(), nullable=False,
            comment="快照：领取批次号",
        ),
        sa.Column(
            "part_serial_no", sa.String(length=100), nullable=True,
            comment="快照：工单流水号（流水号会被释放复用，必须快照）",
        ),
        sa.Column(
            "shelf_id", sa.BigInteger(), nullable=False,
            comment="取件货架 t_shelf.id",
        ),
        sa.Column(
            "work_type_id", sa.BigInteger(), nullable=True,
            comment="工人当时工种 t_work_type.id 快照",
        ),
        sa.Column(
            "quantity", sa.Integer(), nullable=False,
            comment="本次领取数量",
        ),
        sa.Column(
            "part_planned_delivery_date", sa.Date(), nullable=True,
            comment="所取件计划交期；NULL 表示无交期",
        ),
        sa.Column(
            "skipped_earliest_date", sa.Date(), nullable=True,
            comment="被跳过的候选件中最早交期；NULL 表示无可比候选",
        ),
        # EventTimestampMixin：append-only，只带 created_at
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_t_pickup_skip_event_worker_id",
        "t_pickup_skip_event", ["worker_id"],
    )
    op.create_index(
        "ix_t_pickup_skip_event_created_at",
        "t_pickup_skip_event", ["created_at"],
    )


def downgrade() -> None:
    # 逆序回滚
    op.drop_index("ix_t_pickup_skip_event_created_at", table_name="t_pickup_skip_event")
    op.drop_index("ix_t_pickup_skip_event_worker_id", table_name="t_pickup_skip_event")
    op.drop_table("t_pickup_skip_event")
    op.drop_column("t_work_type", "max_held_batches")
