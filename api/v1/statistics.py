"""生产统计 API（MANAGER-only；2026-08-03 新增）。

- GET /statistics/overview              tab1：基础统计 + 图表
- GET /statistics/workers               tab2：所有未软删工人的贡献度一览
- GET /statistics/workers/{worker_id}   tab3：单工人详情（pickup / return / 参与工单）

权限：router 级别 `dependencies=[Depends(require_role(UserRole.MANAGER))]`；
manager 才能访问。

查询参数：
- `date_from` / `date_to` 是 date 类型（闭区间）；
- `worker_id` 是雪花 ID 字符串（service 层 parse_snowflake_id）；
- `date_from > date_to` 由 service 层抛 BIZ_INVALID_VALUE 400。
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from api.deps import get_statistics_service
from core.permission import require_role
from model.enums import UserRole
from schema.statistics import (
    OverviewOut,
    WorkerDetailOut,
    WorkerStatsListOut,
)
from service.statistics import StatisticsService

# router 级守卫：仅 MANAGER；省去每个端点重复 dependencies
router = APIRouter(
    prefix="/statistics",
    tags=["生产统计"],
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)


@router.get(
    "/overview",
    response_model=OverviewOut,
    summary="生产统计概览（MANAGER）",
)
async def get_overview(
    date_from: Annotated[date, Query(description="开始日期（含）")],
    date_to: Annotated[date, Query(description="结束日期（含）")],
    svc: StatisticsService = Depends(get_statistics_service),
) -> OverviewOut:
    return await svc.overview(date_from=date_from, date_to=date_to)


@router.get(
    "/workers",
    response_model=WorkerStatsListOut,
    summary="工人贡献度统计（MANAGER；一次性返回所有未软删工人）",
)
async def get_workers_stats(
    date_from: Annotated[date, Query(description="开始日期（含）")],
    date_to: Annotated[date, Query(description="结束日期（含）")],
    svc: StatisticsService = Depends(get_statistics_service),
) -> WorkerStatsListOut:
    return await svc.worker_stats(date_from=date_from, date_to=date_to)


@router.get(
    "/workers/{worker_id}",
    response_model=WorkerDetailOut,
    summary="单工人详情（MANAGER）",
)
async def get_worker_detail(
    worker_id: Annotated[
        str, Path(description="工人雪花 ID（字符串）"),
    ],
    date_from: Annotated[date, Query(description="开始日期（含）")],
    date_to: Annotated[date, Query(description="结束日期（含）")],
    svc: StatisticsService = Depends(get_statistics_service),
) -> WorkerDetailOut:
    """注意：worker_id 走路径参数（雪花 ID 字符串），由 service
    层 parse_snowflake_id 转换；非法 → 400 BIZ_INVALID_VALUE，不存在/已软删 →
    404 BIZ_WORKER_NOT_FOUND。"""
    return await svc.worker_detail(
        worker_id=worker_id, date_from=date_from, date_to=date_to,
    )
