// 后端生产统计 API（走 @/api/http 统一 axios 客户端）。
//
// 后端约束：
// - MANAGER-only（api/v1/statistics.py router-level require_role(MANAGER)）；
// - 入参 date_from / date_to 必填 'YYYY-MM-DD'；其他字段无；
// - 出参 schema 详见 types/statistics.ts。
//
// 三个端点对应三个 tab：
// - fetchOverview            → tab1 基础统计 + 图表
// - fetchWorkerStats         → tab2 工人贡献度列表
// - fetchWorkerDetail        → tab3 单工人详情（pickup / return / 参与工单）

import { api } from '@/api/http'
import type {
  OverviewOut,
  StatisticsQuery,
  WorkerDetailOut,
  WorkerStatsListOut,
} from '@/types/statistics'

/**
 * GET /statistics/overview
 *
 * 基础统计 + 4 个图表数据（daily_created / daily_completed /
 * delivery_performance / status_distribution）。
 *
 * date_from / date_to 必填；后端要求严格 'YYYY-MM-DD' 格式。
 */
export async function fetchOverview(q: StatisticsQuery): Promise<OverviewOut> {
  const { data } = await api.get<OverviewOut>('/statistics/overview', {
    params: { date_from: q.date_from, date_to: q.date_to },
  })
  return data
}

/**
 * GET /statistics/workers
 *
 * tab2：所有未软删工人的贡献度一览（一次性全量返回，
 * 前端按 is_active / 工种 / pickup_count 自管分页筛选）。
 */
export async function fetchWorkerStats(q: StatisticsQuery): Promise<WorkerStatsListOut> {
  const { data } = await api.get<WorkerStatsListOut>('/statistics/workers', {
    params: { date_from: q.date_from, date_to: q.date_to },
  })
  return data
}

/**
 * GET /statistics/workers/{worker_id}
 *
 * tab3：单工人详情（worker 卡片 + 数字 + daily_pickups + parts 列表）。
 * workerId 是雪花 ID 字符串（非 int，防 Number 精度丢失，见 CLAUDE.md §3）。
 */
export async function fetchWorkerDetail(
  workerId: string,
  q: StatisticsQuery,
): Promise<WorkerDetailOut> {
  const { data } = await api.get<WorkerDetailOut>(
    `/statistics/workers/${encodeURIComponent(workerId)}`,
    {
      params: { date_from: q.date_from, date_to: q.date_to },
    },
  )
  return data
}
