"""生产统计 Pydantic schema（MANAGER-only）。

三个端点：
- GET /statistics/overview          tab1：基础统计 + 图表
- GET /statistics/workers           tab2：所有未软删工人的贡献度一览
- GET /statistics/workers/{worker_id}  tab3：单工人详情（pickup / return / 参与工单）

所有雪花 ID 入参用 `str`（CLAUDE.md §3）；出参用 `IdStr` / `IdStrNonNull`
序列化为字符串。前端 TS 同步 `string`。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from schema._types import IdStr, IdStrNonNull


# ============================================================
# 图表公共原子
# ============================================================
class DayCount(BaseModel):
    """日期 + 计数（daily_created / daily_completed / daily_pickups 共用）。"""

    model_config = ConfigDict(from_attributes=True)

    date: date
    count: int = Field(ge=0)


class DeliveryPerformance(BaseModel):
    """delivery_performance：基于 delivered_count 集合再细分的命中数。

    - on_time : 实际交期 <= 计划交期（不论 system_delivery_date）。
    - orange  : 实际 > 计划 且 (system_delivery IS NULL OR 实际 <= system) — 橙档。
    - red     : system 存在 且 实际 > system — 红档（严重逾期）。

    单件三类互斥：on_time + orange + red == delivered_count。
    """

    on_time: int = Field(ge=0)
    orange: int = Field(ge=0)
    red: int = Field(ge=0)


class StatusCount(BaseModel):
    """status_distribution：当前各状态零件数（status_value, count）。"""

    status_value: str
    count: int = Field(ge=0)


# ============================================================
# tab1 OverviewOut
# ============================================================
class OverviewOut(BaseModel):
    """生产统计概览（MANAGER-only）。"""

    date_from: date
    date_to: date

    # 基础统计
    created_count: int = Field(ge=0, description="期内新建工单数（t_part.created_at ∈ 范围）")
    completed_count: int = Field(
        ge=0,
        description="期内完成工单数（COMPLETED 事件 batch_id IS NULL，count distinct part_id）",
    )
    in_process_count: int = Field(
        ge=0,
        description="期末在制：事件重构（任意 COMPLETED/CANCELLED 工单级事件 created_at < date_to+1 → 排除）",
    )
    delivered_count: int = Field(ge=0, description="期内交付零件数（actual_delivery_date ∈ 范围）")
    delivered_value: Decimal = Field(
        description="期内总产值 sum(total_price)（精确到分，Decimal Pydantic 自动序列化为 string）",
    )
    late_orange_count: int = Field(ge=0, description="橙档：actual > planned 且 (system IS NULL OR actual <= system)")
    late_red_count: int = Field(ge=0, description="红档：system 存在 且 actual > system")
    overdue_undelivered_count: int = Field(
        ge=0,
        description="超期未交付（当前快照，planned < 今天 且 actual IS NULL 且非终态）",
    )
    repair_part_count: int = Field(
        ge=0,
        description="期内返修工单数（REPAIR_STARTED 事件，count distinct part_id）",
    )

    # 图表
    daily_created: list[DayCount] = Field(
        description="每日新建工单数（零填充到 [date_from, date_to]）",
    )
    daily_completed: list[DayCount] = Field(
        description="每日完成工单数（同上，基于 COMPLETED 事件）",
    )
    delivery_performance: DeliveryPerformance = Field(
        description="期内交付表现细粒度拆分（on_time/orange/red）",
    )
    status_distribution: list[StatusCount] = Field(
        description="当前各状态工单数快照",
    )


# ============================================================
# tab2 WorkerStatsListOut
# ============================================================
class WorkerStatsItem(BaseModel):
    """单个工人的贡献度统计。"""

    model_config = ConfigDict(from_attributes=True)

    worker_id: IdStrNonNull
    worker_name: str
    badge_code: str
    work_type_id: IdStr = Field(default=None, description="NULL = 未分配工种")
    work_type_name: str | None = None
    is_active: bool

    pickup_count: int = Field(ge=0, description="期内 PICKED_UP 事件数")
    pickup_quantity: int = Field(ge=0, description="期内 PICKED_UP 事件 sum(quantity)，NULL 自动忽略")
    participated_part_count: int = Field(ge=0, description="期内该工人参与的 distinct 工单数")
    contribution_pct: float | None = Field(
        default=None,
        description="贡献度百分比 0-100；公式见 StatisticsService._compute_contribution",
    )


class WorkerStatsListOut(BaseModel):
    """tab2：工人贡献度列表（一次性返回所有未软删工人，前端分页自管）。"""

    items: list[WorkerStatsItem]


# ============================================================
# tab3 WorkerDetailOut
# ============================================================
class WorkerBrief(BaseModel):
    """tab3 / parts 公共 worker 概览。"""

    id: IdStrNonNull
    name: str
    badge_code: str
    work_type_name: str | None = None
    is_active: bool


class WorkerPartItem(BaseModel):
    """该工人参与过的工单（按 last_pickup_at desc）。

    parts 列表只包含**未软删**工单；领取次数仍计入卡片（即使 part 已软删）。
    """

    part_id: IdStrNonNull
    serial_no: str | None = None
    name: str
    drawing_no: str
    status: str = Field(description="part 当前 status（PartStatus）")
    pickup_count: int = Field(ge=0)
    last_pickup_at: datetime


class WorkerDetailOut(BaseModel):
    """tab3：单工人详情。"""

    worker: WorkerBrief
    pickup_count: int = Field(ge=0)
    pickup_quantity: int = Field(ge=0)
    participated_part_count: int = Field(ge=0)
    return_count: int = Field(ge=0, description="期内 RETURNED 事件数")
    daily_pickups: list[DayCount] = Field(
        description="每日领取次数（零填充到 [date_from, date_to]）",
    )
    parts: list[WorkerPartItem] = Field(
        description="该工人参与工单一览（按 last_pickup_at desc，仅未软删件）",
    )


# ============================================================
# tab4 跳序取件（2026-08-05 新增）
# ============================================================
class PickupSkipSummaryItem(BaseModel):
    """单个工人的跳序取件汇总。"""

    model_config = ConfigDict(from_attributes=True)

    worker_id: IdStrNonNull
    worker_name: str = Field(
        description="工人姓名；工人被软删时回退 '(已删除)' 字符串",
    )
    badge_code: str
    work_type_name: str | None = None
    skip_count: int = Field(ge=0, description="该工人累计跳序次数")
    last_skip_at: datetime | None = Field(
        default=None,
        description="该工人最近一次跳序时间（ISO datetime）",
    )


class PickupSkipSummaryOut(BaseModel):
    """tab4 汇总：所有发生过跳序的工人一览（按 skip_count desc, last_skip_at desc）。"""

    items: list[PickupSkipSummaryItem]


class PickupSkipDetailItem(BaseModel):
    """单个跳序事件明细。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    part_id: IdStrNonNull
    # 流水号快照（可能为 NULL = 该工单已 release serial）
    serial_no: str | None = None
    part_name: str
    batch_no: int = Field(ge=1)
    quantity: int = Field(ge=1)
    part_planned_delivery_date: date | None = None
    skipped_earliest_date: date | None = None
    created_at: datetime


class PickupSkipDetailOut(BaseModel):
    """tab4 明细分页：单工人的全部跳序事件。"""

    items: list[PickupSkipDetailItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
