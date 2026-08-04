"""跳序取件事件表（2026-08-05）。

记录工人跳过更早 ``planned_delivery_date`` 候选件而领取晚交期件的轨迹；
append-only，无 version / updated_at / deleted_at（继承 ``EventTimestampMixin``）。

加急件（``is_urgent=TRUE``）永不记录；其他纯按 ``planned_delivery_date``
比较。

快照字段：
- ``part_serial_no``：工单流水号（释放复用前快照，防历史数据失真）；
- ``batch_no``：领取批次号（拆分后新批次号同步记录）。

字段语义详见迁移 ``000000000029_worktype_limit_and_pickup_skip.py`` 行内
comment。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import EventTimestampMixin
from model.base import Base
from utils.id_gen import new_id


class TPickupSkipEvent(Base, EventTimestampMixin):
    """跳序取件事件：工人跳过更早交期候选件而领取晚交期件的轨迹。

    append-only，无 OCC / 软删。继承 ``EventTimestampMixin`` 自动获得
    ``created_at`` 列（server_default=now()）。
    """

    __tablename__ = "t_pickup_skip_event"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )

    # —— 业务主键 ——
    # 逻辑外键 → t_worker.id；触发跳序的工人
    worker_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 逻辑外键 → t_part.id；本次实际领取的工单
    part_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 逻辑外键 → t_part_batch.id；记录实际领取批次（拆分后为新批次）
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # —— 快照 ——
    # 领取批次号（拆分后写入新批次的 batch_no）
    batch_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 工单流水号（流水号会被释放复用，必须快照）
    part_serial_no: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    # 取件货架 t_shelf.id
    shelf_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 工人当时工种 t_work_type.id 快照
    work_type_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # —— 数量 / 交期 ——
    # 本次领取数量
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # 所取件计划交期；NULL 表示无交期
    part_planned_delivery_date: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    # 被跳过的候选件中最早交期；NULL 表示无可比候选
    skipped_earliest_date: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )

    __table_args__ = (
        # worker_id 索引：按工人聚合（统计端点 GROUP BY worker_id）
        Index(
            "ix_t_pickup_skip_event_worker_id",
            "worker_id",
        ),
        # created_at 索引：明细按时间倒序 + 时间窗口过滤
        Index(
            "ix_t_pickup_skip_event_created_at",
            "created_at",
        ),
    )