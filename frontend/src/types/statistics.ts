// 生产统计 TS 类型（严格对齐 backend/schema/statistics.py）。
//
// 所有雪花 ID 都是 string（CLAUDE.md §3：出参走 IdStr/IdStrNonNull 序列化为字符串）；
// decimal 字段（Pydantic 自动序列化为 string，避免 JS 精度丢失）。
// 日期 / 时间字段遵循后端 ISO 序列化：date='YYYY-MM-DD'，datetime='YYYY-MM-DDTHH:mm:ss[.ffffff][+HH:MM]'。

// ============================================================
// 图表公共原子
// ============================================================

/** 日期 + 计数（daily_created / daily_completed / daily_pickups 共用）。 */
export interface DayCount {
  date: string
  count: number
}

/** 期内交付表现细粒度拆分（on_time/orange/red 互斥，且 == delivered_count）。 */
export interface DeliveryPerformance {
  on_time: number
  orange: number
  red: number
}

/** status_distribution：当前各状态零件数（status_value, count）。
 *  字段名 status_value 而非 status，以对齐后端 Pydantic 命名。 */
export interface StatusCount {
  status_value: string
  count: number
}

// ============================================================
// GET /statistics/overview
// ============================================================

export interface OverviewOut {
  date_from: string
  date_to: string

  // 基础统计
  created_count: number
  completed_count: number
  in_process_count: number
  delivered_count: number
  /** 期内总产值 sum(total_price)，Decimal → string，保留两位小数精度。 */
  delivered_value: string
  late_orange_count: number
  late_red_count: number
  overdue_undelivered_count: number
  repair_part_count: number

  // 图表
  daily_created: DayCount[]
  daily_completed: DayCount[]
  delivery_performance: DeliveryPerformance
  status_distribution: StatusCount[]
}

// ============================================================
// GET /statistics/workers
// ============================================================

/** 单个工人的贡献度统计。 */
export interface WorkerStatsItem {
  worker_id: string
  worker_name: string
  badge_code: string
  /** NULL = 未分配工种 */
  work_type_id: string | null
  work_type_name: string | null
  is_active: boolean

  pickup_count: number
  pickup_quantity: number
  participated_part_count: number
  /** 贡献度百分比 0-100；无工种或 0 总量 → null。 */
  contribution_pct: number | null
}

export interface WorkerStatsListOut {
  items: WorkerStatsItem[]
}

// ============================================================
// GET /statistics/workers/{worker_id}
// ============================================================

/** tab3 单工人概览（不含 work_type_id：详情页用不到，仅展示名称）。 */
export interface WorkerBrief {
  id: string
  name: string
  badge_code: string
  work_type_name: string | null
  is_active: boolean
}

/** 该工人参与过的工单（按 last_pickup_at desc，仅未软删件）。
 *  领取次数仍计入卡片（即使 part 已软删不影响 pickup_count 历史）。 */
export interface WorkerPartItem {
  part_id: string
  /** NULL = 流水号已被释放（如 COMPLETED / CANCELLED 回池）。 */
  serial_no: string | null
  name: string
  drawing_no: string
  /** 当前 status（PartStatus 枚举值字符串）。 */
  status: string
  pickup_count: number
  /** ISO datetime；上次该工人领取该工单的时间。 */
  last_pickup_at: string
}

export interface WorkerDetailOut {
  worker: WorkerBrief
  pickup_count: number
  pickup_quantity: number
  participated_part_count: number
  /** 期内 RETURNED 事件数。 */
  return_count: number
  /** 每日领取次数（零填充到 [date_from, date_to]）。 */
  daily_pickups: DayCount[]
  /** 该工人参与工单一览（按 last_pickup_at desc，仅未软删件）。 */
  parts: WorkerPartItem[]
}

// ============================================================
// ============================================================
// GET /statistics/pickup-skips
// ============================================================

/** tab4 单个工人的跳序取件汇总。 */
export interface PickupSkipSummaryItem {
  worker_id: string
  /** 工人姓名；工人被软删时回退 '(已删除)' 字符串 */
  worker_name: string
  badge_code: string
  work_type_name: string | null
  /** 该工人累计跳序次数 */
  skip_count: number
  /** 该工人最近一次跳序时间（ISO datetime；从未跳序则 null） */
  last_skip_at: string | null
}

export interface PickupSkipSummaryOut {
  items: PickupSkipSummaryItem[]
}

// ============================================================
// GET /statistics/pickup-skips/{worker_id}
// ============================================================

/** tab4 单条跳序事件明细。 */
export interface PickupSkipDetailItem {
  id: string
  part_id: string
  /** 流水号快照（可能为 null = 该工单已 release serial） */
  serial_no: string | null
  part_name: string
  batch_no: number
  quantity: number
  part_planned_delivery_date: string | null
  skipped_earliest_date: string | null
  created_at: string
}

export interface PickupSkipDetailOut {
  items: PickupSkipDetailItem[]
  total: number
  limit: number
  offset: number
}

// ============================================================
// 公共查询参数
// ============================================================

/** GET /statistics/* 的日期范围参数。date_from / date_to 必填，'YYYY-MM-DD'。 */
export interface StatisticsQuery {
  date_from: string
  date_to: string
}

/** GET /statistics/pickup-skips/{worker_id} 的分页参数。 */
export interface PickupSkipDetailQuery {
  limit: number
  offset: number
}
