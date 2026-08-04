"""生产统计只读仓储（2026-08-03 新增；2026-08-05 增跳序取件聚合）。

所有方法都返回聚合 tuple / dict，**不**返回 ORM 实体，避免在 async
session 中触发 lazy load（CLAUDE.md §13）。service 层做零填充 / 拼装。

口径参见 `schema/statistics.py` 的字段注释，端点契约集中审视。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart, TPartEvent, TPickupSkipEvent, TWorker, TWorkType
from model.enums import PartEventType, PartStatus


@dataclass
class WorkerPickupRow:
    """worker_pickup_rows 的原始行（contribution 聚合前置）。"""

    worker_id: int
    work_type_id: int | None
    pickup_count: int
    pickup_quantity: int
    participated_part_count: int


@dataclass
class WorkerPartRow:
    """worker_parts 的原始行（service 层用键名查找，写明字段避免 dict + object）。"""

    part_id: int
    serial_no: str | None
    name: str
    drawing_no: str
    status: str
    pickup_count: int
    last_pickup_at: datetime


@dataclass
class PickupSkipSummaryRow:
    """pickup_skip_summary 的原始行（按工人聚合）。"""

    worker_id: int
    worker_name: str
    badge_code: str
    work_type_name: str | None
    skip_count: int
    last_skip_at: datetime | None


@dataclass
class PickupSkipDetailRow:
    """pickup_skip_detail 的原始行（单工人跳序事件明细）。"""

    id: int
    part_id: int
    serial_no: str | None
    part_name: str
    batch_no: int
    quantity: int
    part_planned_delivery_date: date | None
    skipped_earliest_date: date | None
    created_at: datetime


class StatisticsRepository:
    """生产统计查询仓储（单 session 共享）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ============================================================
    # tab1：基础计数
    # ============================================================
    async def count_created(
        self, date_from: date, date_to: date,
    ) -> int:
        """期内新建工单数：t_part.created_at ∈ [date_from, date_to+1)，deleted_at IS NULL。"""
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(func.count(TPart.id))
            .where(TPart.deleted_at.is_(None))
            .where(TPart.created_at >= dt_from)
            .where(TPart.created_at < dt_to_exclusive)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_completed(
        self, date_from: date, date_to: date,
    ) -> int:
        """期内完成工单数：event_type=COMPLETED 且 batch_id IS NULL，distinct part_id。"""
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(func.count(func.distinct(TPartEvent.part_id)))
            .where(TPartEvent.event_type == PartEventType.COMPLETED.value)
            .where(TPartEvent.batch_id.is_(None))
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_in_process_at(self, date_to: date) -> int:
        """期末在制（事件重构）：在 date_to+1 时刻之前已创建、且**不存在**
        COMPLETED/CANCELLED 工单级事件的工单数。

        即：t_part.created_at < date_to+1 AND deleted_at IS NULL
        AND NOT EXISTS (工单级 COMPLETED/CANCELLED 事件 created_at < date_to+1)

        事件重构保证「已 cancel/complete 的不计入」语义稳定，不依赖
        `t_part.status` 列（部分跨状态迁移曾让该列与事件流短暂不一致）。
        """
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        # NOT EXISTS 子查询：同一 part 存在工单级终止事件
        stmt = (
            select(func.count(TPart.id))
            .where(TPart.deleted_at.is_(None))
            .where(TPart.created_at < dt_to_exclusive)
            .where(not_(select(TPartEvent.id).where(
                and_(
                    TPartEvent.part_id == TPart.id,
                    TPartEvent.batch_id.is_(None),
                    TPartEvent.event_type.in_([
                        PartEventType.COMPLETED.value,
                        PartEventType.CANCELLED.value,
                    ]),
                    TPartEvent.created_at < dt_to_exclusive,
                ),
            ).exists()))
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def delivered_stats(
        self, date_from: date, date_to: date,
    ) -> tuple[int, Decimal, int, int]:
        """期内交付集合一次扫表返回 (count, sum_total_price, orange, red)。

        - count            : count(id) WHERE actual_delivery_date ∈ [from, to_inclusive]
        - sum_total_price  : sum(total_price) (Decimal)
        - orange           : count WHERE actual > planned AND (system IS NULL OR actual <= system)
        - red              : count WHERE system IS NOT NULL AND actual > system

        单条 SQL 用 `filter / case` 条件聚合，避免来回 round-trip。
        """
        late_orange = case(
            (
                and_(
                    TPart.actual_delivery_date > TPart.planned_delivery_date,
                    or_(
                        TPart.system_delivery_date.is_(None),
                        TPart.actual_delivery_date <= TPart.system_delivery_date,
                    ),
                ),
                1,
            ),
            else_=0,
        )
        late_red = case(
            (
                and_(
                    TPart.system_delivery_date.is_not(None),
                    TPart.actual_delivery_date > TPart.system_delivery_date,
                ),
                1,
            ),
            else_=0,
        )
        stmt = (
            select(
                func.count(TPart.id),
                func.coalesce(func.sum(TPart.total_price), 0),
                func.coalesce(func.sum(late_orange), 0),
                func.coalesce(func.sum(late_red), 0),
            )
            .where(TPart.deleted_at.is_(None))
            .where(TPart.actual_delivery_date >= date_from)
            .where(TPart.actual_delivery_date <= date_to)
        )
        row = (await self.session.execute(stmt)).one()
        count_int = int(row[0] or 0)
        sum_decimal = row[1] if row[1] is not None else Decimal(0)
        if not isinstance(sum_decimal, Decimal):
            sum_decimal = Decimal(str(sum_decimal))
        return count_int, sum_decimal, int(row[2] or 0), int(row[3] or 0)

    async def daily_created_counts(
        self, date_from: date, date_to: date,
    ) -> dict[date, int]:
        """期内每日新建工单数 → dict[date, int]，缺日期不出现（service 零填充）。"""
        from datetime import timedelta
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(
                func.date(TPart.created_at).label("d"),
                func.count(TPart.id),
            )
            .where(TPart.deleted_at.is_(None))
            .where(TPart.created_at >= dt_from)
            .where(TPart.created_at < dt_to_exclusive)
            .group_by(func.date(TPart.created_at))
        )
        rows = (await self.session.execute(stmt)).all()
        return {d: int(n) for d, n in rows}

    async def daily_completed_counts(
        self, date_from: date, date_to: date,
    ) -> dict[date, int]:
        """期内每日 COMPLETED 事件数（批定 created_at 日，按事件聚合；同表
        daily_created 用 SQL 形式对称）。

        注意：这里返回的是**事件数**（不 distinct part_id），与 daily_created
        用 count(id) 对齐。前端展示用事件数即可。
        """
        from datetime import timedelta
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(
                func.date(TPartEvent.created_at).label("d"),
                func.count(TPartEvent.id),
            )
            .where(TPartEvent.event_type == PartEventType.COMPLETED.value)
            .where(TPartEvent.batch_id.is_(None))
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
            .group_by(func.date(TPartEvent.created_at))
        )
        rows = (await self.session.execute(stmt)).all()
        return {d: int(n) for d, n in rows}

    async def count_repair_parts(
        self, date_from: date, date_to: date,
    ) -> int:
        """期内返修工单数（event_type=REPAIR_STARTED，count distinct part_id）。"""
        from datetime import timedelta
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(func.count(func.distinct(TPartEvent.part_id)))
            .where(TPartEvent.event_type == PartEventType.REPAIR_STARTED.value)
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_overdue_undelivered(self, today: date) -> int:
        """当前快照：超期未交付工单数。

        - planned_delivery_date < today
        - actual_delivery_date IS NULL
        - status NOT IN (COMPLETED, CANCELLED)
        - deleted_at IS NULL
        """
        stmt = (
            select(func.count(TPart.id))
            .where(TPart.deleted_at.is_(None))
            .where(TPart.planned_delivery_date < today)
            .where(TPart.actual_delivery_date.is_(None))
            .where(
                TPart.status.notin_([
                    PartStatus.COMPLETED.value,
                    PartStatus.CANCELLED.value,
                ])
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def status_distribution(self) -> list[tuple[str, int]]:
        """当前各 status 工单数 → [(status_value, count), ...]。

        只看 deleted_at IS NULL；终态 CANCELLED 也带上便于前端看分布。
        """
        stmt = (
            select(TPart.status, func.count(TPart.id))
            .where(TPart.deleted_at.is_(None))
            .group_by(TPart.status)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(s, int(n)) for s, n in rows]

    # ============================================================
    # tab2：工人 pickup 聚合
    # ============================================================
    async def worker_pickup_rows(
        self, date_from: date, date_to: date,
    ) -> list[WorkerPickupRow]:
        """期内每个工人的 PICKED_UP 聚合：(worker_id, work_type_id, pickup_count,
        pickup_quantity, participated_part_count)。

        一条 SQL 用 `GROUP BY worker_id` + `count(distinct part_id)` + `sum(quantity)`
        一次性拿下，避免 N+1。quantity 是事件上挂的本次领取数（批次事件可能 NULL；
        SQL SUM 自动忽略 NULL）。distinct part_id 反映「期内科次参与的工单数」。
        """
        from datetime import timedelta
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(
                TPartEvent.worker_id.label("worker_id"),
                TWorker.work_type_id.label("work_type_id"),
                func.count(TPartEvent.id).label("pickup_count"),
                func.coalesce(func.sum(TPartEvent.quantity), 0).label("pickup_qty"),
                func.count(func.distinct(TPartEvent.part_id)).label("participated_part_count"),
            )
            .select_from(TPartEvent)
            .join(TWorker, TWorker.id == TPartEvent.worker_id)
            .where(TPartEvent.event_type == PartEventType.PICKED_UP.value)
            .where(TPartEvent.worker_id.is_not(None))
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
            .group_by(TPartEvent.worker_id, TWorker.work_type_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            WorkerPickupRow(
                worker_id=int(r.worker_id),
                work_type_id=int(r.work_type_id) if r.work_type_id is not None else None,
                pickup_count=int(r.pickup_count or 0),
                pickup_quantity=int(r.pickup_qty or 0),
                participated_part_count=int(r.participated_part_count or 0),
            )
            for r in rows
        ]

    # ============================================================
    # tab3：单工人详情
    # ============================================================
    async def worker_detail_events(
        self, worker_id: int, date_from: date, date_to: date,
    ) -> tuple[int, int, int]:
        """单工人在区间内 (pickup_count, pickup_quantity, return_count)。

        pickup_count  : PICKED_UP 事件数
        pickup_quantity : 同上 sum(quantity)
        return_count  : RETURNED 事件数
        """
        from datetime import timedelta
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        pickup_qty = func.coalesce(
            func.sum(
                case(
                    (TPartEvent.event_type == PartEventType.PICKED_UP.value,
                     TPartEvent.quantity),
                    else_=None,
                )
            ),
            0,
        )
        pickup_cnt = func.coalesce(
            func.sum(
                case(
                    (TPartEvent.event_type == PartEventType.PICKED_UP.value, 1),
                    else_=0,
                )
            ),
            0,
        )
        return_cnt = func.coalesce(
            func.sum(
                case(
                    (TPartEvent.event_type == PartEventType.RETURNED.value, 1),
                    else_=0,
                )
            ),
            0,
        )
        stmt = (
            select(pickup_cnt, pickup_qty, return_cnt)
            .where(TPartEvent.worker_id == worker_id)
            .where(TPartEvent.event_type.in_([
                PartEventType.PICKED_UP.value,
                PartEventType.RETURNED.value,
            ]))
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
        )
        row = (await self.session.execute(stmt)).one()
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    async def worker_daily_pickups(
        self, worker_id: int, date_from: date, date_to: date,
    ) -> dict[date, int]:
        """单工人每日 PICKED_UP 次数 → dict[date, int]（缺日期不出现，service 零填充）。"""
        from datetime import timedelta
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        stmt = (
            select(
                func.date(TPartEvent.created_at).label("d"),
                func.count(TPartEvent.id),
            )
            .where(TPartEvent.worker_id == worker_id)
            .where(TPartEvent.event_type == PartEventType.PICKED_UP.value)
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
            .group_by(func.date(TPartEvent.created_at))
        )
        rows = (await self.session.execute(stmt)).all()
        return {d: int(n) for d, n in rows}

    async def worker_parts(
        self, worker_id: int, date_from: date, date_to: date,
    ) -> list[WorkerPartRow]:
        """单工人期内参与工单一览（含 last_pickup_at / 该工人对该工单领取次数）。

        每条 part 一行：连表 t_part（保留未软删行的全部内容）+ 子聚合统计
        「该工人对该 part 的 PICKED_UP 数」+ 「该工人最近一次 PICKED_UP 时间」。

        已软删件不在结果中（PART 软删后前端不应再列出），但领取次数仍计入
        tab3 总数卡片——这部分由 tab3 service 层基于全部事件（不 JOIN part）
        计算。
        """
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to_exclusive = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
        # 该工人对该 part 的取件计数 + 最大时间（先对该工人全部 PICKED_UP 聚合）
        per_part_subq = (
            select(
                TPartEvent.part_id.label("part_id"),
                func.count(TPartEvent.id).label("per_part_pickup_count"),
                func.max(TPartEvent.created_at).label("last_pickup_at"),
            )
            .where(TPartEvent.worker_id == worker_id)
            .where(TPartEvent.event_type == PartEventType.PICKED_UP.value)
            .where(TPartEvent.created_at >= dt_from)
            .where(TPartEvent.created_at < dt_to_exclusive)
            .group_by(TPartEvent.part_id)
            .subquery()
        )
        stmt = (
            select(
                TPart.id.label("part_id"),
                TPart.serial_no,
                TPart.name,
                TPart.drawing_no,
                TPart.status,
                per_part_subq.c.per_part_pickup_count,
                per_part_subq.c.last_pickup_at,
            )
            .select_from(per_part_subq)
            .join(TPart, TPart.id == per_part_subq.c.part_id)
            .where(TPart.deleted_at.is_(None))
            .order_by(per_part_subq.c.last_pickup_at.desc(), TPart.id.desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            WorkerPartRow(
                part_id=int(r.part_id),
                serial_no=r.serial_no,
                name=r.name or "",
                drawing_no=r.drawing_no or "",
                status=r.status,
                pickup_count=int(r.per_part_pickup_count or 0),
                last_pickup_at=r.last_pickup_at,
            )
            for r in rows
        ]  # type: ignore[misc]

    # ============================================================
    # tab4 跳序取件（2026-08-05 新增）
    # ============================================================
    async def pickup_skip_summary(self) -> list[PickupSkipSummaryRow]:
        """按工人聚合的跳序次数 + 最近跳序时间。

        LEFT JOIN t_worker / t_work_type 拿名称；工人被软删 → 名称兜底 '(已删除)'，
        工牌回退空串（典型情况：worker 已停用但事件仍存在）。
        GROUP BY worker_id + worker_name + badge_code + work_type_name。
        排序：skip_count DESC, last_skip_at DESC（最"活跃"跳序工人在前）。
        """
        stmt = (
            select(
                TPickupSkipEvent.worker_id.label("worker_id"),
                func.coalesce(TWorker.name, "(已删除)").label("worker_name"),
                func.coalesce(TWorker.badge_code, "").label("badge_code"),
                TWorkType.name.label("work_type_name"),
                func.count(TPickupSkipEvent.id).label("skip_count"),
                func.max(TPickupSkipEvent.created_at).label("last_skip_at"),
            )
            .select_from(TPickupSkipEvent)
            .outerjoin(
                TWorker,
                TWorker.id == TPickupSkipEvent.worker_id,
            )
            .outerjoin(
                TWorkType,
                TWorkType.id == TPickupSkipEvent.work_type_id,
            )
            .group_by(
                TPickupSkipEvent.worker_id,
                TWorker.name,
                TWorker.badge_code,
                TWorkType.name,
            )
            .order_by(
                func.count(TPickupSkipEvent.id).desc(),
                func.max(TPickupSkipEvent.created_at).desc(),
            )
        )

        rows = (await self.session.execute(stmt)).all()
        return [
            PickupSkipSummaryRow(
                worker_id=int(r.worker_id),
                worker_name=r.worker_name or "(已删除)",
                badge_code=r.badge_code or "",
                work_type_name=r.work_type_name,
                skip_count=int(r.skip_count or 0),
                last_skip_at=r.last_skip_at,
            )
            for r in rows
        ]

    async def pickup_skip_detail(
        self, *, worker_id: int, limit: int, offset: int,
    ) -> list[PickupSkipDetailRow]:
        """单工人跳序事件明细分页（按 created_at desc）。"""
        stmt = (
            select(
                TPickupSkipEvent.id.label("id"),
                TPickupSkipEvent.part_id.label("part_id"),
                TPickupSkipEvent.part_serial_no.label("serial_no"),
                func.coalesce(TPart.name, "(已删除)").label("part_name"),
                TPickupSkipEvent.batch_no.label("batch_no"),
                TPickupSkipEvent.quantity.label("quantity"),
                TPickupSkipEvent.part_planned_delivery_date.label(
                    "part_planned_delivery_date"
                ),
                TPickupSkipEvent.skipped_earliest_date.label(
                    "skipped_earliest_date"
                ),
                TPickupSkipEvent.created_at.label("created_at"),
            )
            .select_from(TPickupSkipEvent)
            .outerjoin(
                TPart,
                TPart.id == TPickupSkipEvent.part_id,
            )
            .where(TPickupSkipEvent.worker_id == worker_id)
            .order_by(
                TPickupSkipEvent.created_at.desc(),
                TPickupSkipEvent.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            PickupSkipDetailRow(
                id=int(r.id),
                part_id=int(r.part_id),
                serial_no=r.serial_no,
                part_name=r.part_name or "(已删除)",
                batch_no=int(r.batch_no or 0),
                quantity=int(r.quantity or 0),
                part_planned_delivery_date=r.part_planned_delivery_date,
                skipped_earliest_date=r.skipped_earliest_date,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def pickup_skip_detail_count(self, *, worker_id: int) -> int:
        """单工人跳序事件总数（同 pickup_skip_detail 的 worker_id 谓词）。"""
        stmt = (
            select(func.count(TPickupSkipEvent.id))
            .where(TPickupSkipEvent.worker_id == worker_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)
