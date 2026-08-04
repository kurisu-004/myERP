"""生产统计 service（MANAGER-only；2026-08-03 新增；2026-08-05 增跳序取件）。

三段：overview / worker_stats / worker_detail。
- 日期统一转 `[date_from, date_to+1)` 半开区间做 created_at 比较；
- 入参 `worker_id` 走 `parse_snowflake_id`（CLAUDE.md §3）；
- 贡献度公式隔离在 ``_compute_contribution`` 单方法中，后续口径调整只改一处。

2026-08-05：跳序取件两段（summary / detail）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from repository.statistics import (
    PickupSkipDetailRow,
    PickupSkipSummaryRow,
    StatisticsRepository,
    WorkerPartRow,
    WorkerPickupRow,
)
from schema.statistics import (
    DayCount,
    DeliveryPerformance,
    OverviewOut,
    PickupSkipDetailItem,
    PickupSkipDetailOut,
    PickupSkipSummaryItem,
    PickupSkipSummaryOut,
    StatusCount,
    WorkerBrief,
    WorkerDetailOut,
    WorkerPartItem,
    WorkerStatsItem,
    WorkerStatsListOut,
)
from service._id_parse import parse_snowflake_id

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.permission import CurrentUser
    from repository.part import PartRepository
    from repository.part_event import PartEventRepository
    from repository.work_type import WorkTypeRepository
    from repository.worker import WorkerRepository


# ============================================================
# 入参校验
# ============================================================
def _validate_date_range(date_from: date, date_to: date) -> None:
    """`date_from > date_to` 抛 400；`==` 允许（单日查询合法）。"""
    if date_from > date_to:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=(
                f"date_from ({date_from}) must be <= date_to ({date_to})"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )


# ============================================================
# StatisticsService
# ============================================================
class StatisticsService:
    """生产统计 service（MANAGER-only）。

    仅读路径，不写 DB，不广播 dashboard（CLAUDE.md §运营约定：
    statistics 不应触发 dashboard 重推）。
    """

    def __init__(
        self,
        session: AsyncSession,
        stats_repo: StatisticsRepository,
        parts: PartRepository,
        events: PartEventRepository,
        workers: WorkerRepository,
        work_types: WorkTypeRepository,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.session = session
        self.stats = stats_repo
        self.parts = parts
        self.events = events
        self.workers = workers
        self.work_types = work_types
        # 暂未消费；保留接口便于后续在 service 层实现 created_by 记录
        # 与限流。stats 端点无写操作，_user_id 仅供将来扩展。
        self._user_id = (
            current_user.id if current_user and hasattr(current_user, "id")
            else None
        )

    # ============================================================
    # tab1: Overview
    # ============================================================
    async def overview(
        self, date_from: date, date_to: date,
    ) -> OverviewOut:
        _validate_date_range(date_from, date_to)
        today = now_naive().date()

        # 并发跑（Python 异步调度顺序无所谓；每条 SELECT 都是独立 round-trip）
        # 显式标注每条协程的返回类型，便于 mypy 推导 asyncio.gather 的 tuple elements。
        created_count: int = await self.stats.count_created(date_from, date_to)
        completed_count: int = await self.stats.count_completed(date_from, date_to)
        in_process_count: int = await self.stats.count_in_process_at(date_to)
        (
            delivered_count, delivered_value, orange, red,
        ) = await self.stats.delivered_stats(date_from, date_to)
        overdue_undelivered: int = await self.stats.count_overdue_undelivered(today)
        repair_count: int = await self.stats.count_repair_parts(date_from, date_to)
        daily_created_raw: dict[date, int] = await self.stats.daily_created_counts(
            date_from, date_to,
        )
        daily_completed_raw: dict[date, int] = (
            await self.stats.daily_completed_counts(date_from, date_to)
        )
        status_dist: list[tuple[str, int]] = await self.stats.status_distribution()

        # 零填充：service 层循环 date_from..date_to
        daily_created = _zero_fill_day_count(
            date_from, date_to, daily_created_raw,
        )
        daily_completed = _zero_fill_day_count(
            date_from, date_to, daily_completed_raw,
        )

        # on_time = delivered - orange - red（互斥拆分）
        on_time = max(delivered_count - orange - red, 0)

        return OverviewOut(
            date_from=date_from,
            date_to=date_to,
            created_count=created_count,
            completed_count=completed_count,
            in_process_count=in_process_count,
            delivered_count=delivered_count,
            delivered_value=delivered_value,
            late_orange_count=orange,
            late_red_count=red,
            overdue_undelivered_count=overdue_undelivered,
            repair_part_count=repair_count,
            daily_created=daily_created,
            daily_completed=daily_completed,
            delivery_performance=DeliveryPerformance(
                on_time=on_time, orange=orange, red=red,
            ),
            status_distribution=[
                StatusCount(status_value=s, count=c) for s, c in status_dist
            ],
        )

    # ============================================================
    # tab2: WorkerStats
    # ============================================================
    async def worker_stats(
        self, date_from: date, date_to: date,
    ) -> WorkerStatsListOut:
        _validate_date_range(date_from, date_to)

        # 全部未软删工人（不分页，因为页大小限制 500 < 内部工人规模）
        workers_rows = await self.workers.list_with_filters(
            include_deleted=False,
            limit=1000, offset=0,
        )

        # 取工种（name）批查
        work_type_ids = list({
            w.work_type_id for w in workers_rows if w.work_type_id is not None
        })
        wt_rows = (
            await self.work_types.list_by_ids(work_type_ids)
            if work_type_ids else []
        )
        wt_name_map: dict[int, str] = {wt.id: wt.name for wt in wt_rows}

        # 期内 pickup 聚合
        rows = await self.stats.worker_pickup_rows(date_from, date_to)
        agg_map: dict[int, WorkerPickupRow] = {r.worker_id: r for r in rows}

        # 贡献度计算（隔离在 _compute_contribution）
        contribution_map = self._compute_contribution(rows)

        items: list[WorkerStatsItem] = []
        for w in workers_rows:
            agg = agg_map.get(w.id)
            wt_name = (
                wt_name_map.get(w.work_type_id) if w.work_type_id else None
            )
            items.append(WorkerStatsItem(
                worker_id=w.id,
                worker_name=w.name,
                badge_code=w.badge_code,
                work_type_id=w.work_type_id,
                work_type_name=wt_name,
                is_active=bool(w.is_active),
                pickup_count=agg.pickup_count if agg else 0,
                pickup_quantity=agg.pickup_quantity if agg else 0,
                participated_part_count=(
                    agg.participated_part_count if agg else 0
                ),
                contribution_pct=contribution_map.get(w.id),
            ))

        return WorkerStatsListOut(items=items)

    # ============================================================
    # tab3: WorkerDetail
    # ============================================================
    async def worker_detail(
        self, worker_id: str, date_from: date, date_to: date,
    ) -> WorkerDetailOut:
        _validate_date_range(date_from, date_to)
        wid_int = parse_snowflake_id(worker_id, field_name="worker_id")
        # parse_snowflake_id 仅在 value 非空时返 None → 此处必为 int
        assert wid_int is not None, "worker_id parse failed (non-empty input)"
        worker = await self.workers.get_by_id(wid_int)
        if worker is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker {worker_id} not found or deleted",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 工种名
        work_type_name: str | None = None
        if worker.work_type_id:
            wt = await self.work_types.get_by_id(worker.work_type_id)
            work_type_name = wt.name if wt else None

        # 卡片聚合（pickup / return / daily_pickups）+ parts 列表逐条 await，
        # 显式标注返回类型，避开 mypy 对 asyncio.gather tuple-derive 的 object 抱怨。
        events_pair: tuple[int, int, int] = await self.stats.worker_detail_events(
            wid_int, date_from, date_to,
        )
        daily_pickups_raw: dict[date, int] = await self.stats.worker_daily_pickups(
            wid_int, date_from, date_to,
        )
        parts_rows: list[WorkerPartRow] = await self.stats.worker_parts(
            wid_int, date_from, date_to,
        )
        pickup_count, pickup_quantity, return_count = events_pair

        daily_pickups = _zero_fill_day_count(
            date_from, date_to, daily_pickups_raw,
        )

        # distinct part_id（即便 part 已软删也计数；卡片总览与 parts 列表口径不同）
        participated_part_count = len({r.part_id for r in parts_rows})
        # parts_rows 已 JOIN TPart 并过滤 deleted_at IS NULL；
        # 若需要含软删件数，再加一条独立 COUNT(DISTINCT) 查询；这里与 parts 列表口径对齐。

        items = [
            WorkerPartItem(
                part_id=r.part_id,
                serial_no=r.serial_no,
                name=r.name,
                drawing_no=r.drawing_no,
                status=r.status,
                pickup_count=r.pickup_count,
                last_pickup_at=r.last_pickup_at,
            )
            for r in parts_rows
        ]

        return WorkerDetailOut(
            worker=WorkerBrief(
                id=worker.id,
                name=worker.name,
                badge_code=worker.badge_code,
                work_type_name=work_type_name,
                is_active=bool(worker.is_active),
            ),
            pickup_count=pickup_count,
            pickup_quantity=pickup_quantity,
            participated_part_count=participated_part_count,
            return_count=return_count,
            daily_pickups=daily_pickups,
            parts=items,
        )

    # ============================================================
    # 隔离：贡献度公式（**后续会改口径，只动此方法**）
    # ============================================================
    def _compute_contribution(
        self, rows: list[WorkerPickupRow],
    ) -> dict[int, float | None]:
        """贡献度 = 工人领取次数 / 同工种全部工人领取次数之和。

        返回 ``{worker_id: pct (0-100) | None}``：
        - 无工种的工人 → None
        - 工种总领取为 0（无任何活跃领取）→ None

        ⚠️ 公式待业务方后续调整，改时只动这一个方法。

        实现要点：
        - 一次循环按 work_type_id 聚合 total；同工种内每个工人的贡献度
          ``worker.pickup_count / total * 100``，保留 2 位小数。
        - 工人本身无工种（work_type_id None）→ 返回 None，前端展示 "—"。
        """
        # 按 work_type_id 聚合总领取次数
        totals_by_wt: dict[int, int] = {}
        workers_by_wt: dict[int, list[WorkerPickupRow]] = {}
        no_work_type: list[WorkerPickupRow] = []
        for r in rows:
            if r.work_type_id is None:
                no_work_type.append(r)
                continue
            totals_by_wt[r.work_type_id] = (
                totals_by_wt.get(r.work_type_id, 0) + r.pickup_count
            )
            workers_by_wt.setdefault(r.work_type_id, []).append(r)

        out: dict[int, float | None] = {}
        for r in no_work_type:
            out[r.worker_id] = None
        for wt_id, total in totals_by_wt.items():
            for r in workers_by_wt.get(wt_id, []):
                if total <= 0:
                    out[r.worker_id] = None
                else:
                    pct = round(r.pickup_count / total * 100, 2)
                    out[r.worker_id] = pct
        return out

    # ============================================================
    # helper：list_with_filters 边界替代（拿到全量工人）
    # ============================================================
    # (none required: worker_stats 直接调 WorkerRepository.list_with_filters)


# ============================================================
# tab4 跳序取件（2026-08-05 新增）
# ============================================================
    async def pickup_skip_summary(self) -> PickupSkipSummaryOut:
        """按工人聚合的跳序次数一览。

        无日期范围——跳序事件是 append-only 历史流；统计端点返回「全部历史」。
        单条 SQL GROUP BY 已经在 repository 完成。
        """
        rows = await self.stats.pickup_skip_summary()
        items = [
            PickupSkipSummaryItem(
                worker_id=r.worker_id,
                worker_name=r.worker_name,
                badge_code=r.badge_code,
                work_type_name=r.work_type_name,
                skip_count=r.skip_count,
                last_skip_at=r.last_skip_at,
            )
            for r in rows
        ]
        return PickupSkipSummaryOut(items=items)

    async def pickup_skip_detail(
        self, worker_id: str, *, limit: int, offset: int,
    ) -> PickupSkipDetailOut:
        """单工人跳序事件明细分页。

        `worker_id` 入参是雪花 ID 字符串（CLAUDE.md §3），service 层
        `parse_snowflake_id` 转换；非法 → 400 BIZ_INVALID_VALUE。
        """
        wid_int = parse_snowflake_id(worker_id, field_name="worker_id")
        if wid_int is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"worker_id 必须是数字字符串：{worker_id!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        rows = await self.stats.pickup_skip_detail(
            worker_id=wid_int, limit=limit, offset=offset,
        )
        total = await self.stats.pickup_skip_detail_count(worker_id=wid_int)
        items = [
            PickupSkipDetailItem(
                id=r.id,
                part_id=r.part_id,
                serial_no=r.serial_no,
                part_name=r.part_name,
                batch_no=r.batch_no,
                quantity=r.quantity,
                part_planned_delivery_date=r.part_planned_delivery_date,
                skipped_earliest_date=r.skipped_earliest_date,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return PickupSkipDetailOut(
            items=items, total=total, limit=limit, offset=offset,
        )

# ============================================================
# 零填充工具（与 dashboard._fetch_upcoming_delivery 同模式）
# ============================================================
def _zero_fill_day_count(
    date_from: date, date_to: date, raw: dict[date, int],
) -> list[DayCount]:
    """把 ``raw: {date: count}`` 按 [date_from, date_to] 闭区间补齐为 0。"""
    out: list[DayCount] = []
    cur = date_from
    while cur <= date_to:
        out.append(DayCount(date=cur, count=int(raw.get(cur, 0))))
        cur = cur + timedelta(days=1)
    return out
