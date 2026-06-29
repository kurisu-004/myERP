// 大屏 WebSocket 数据类型。包含 snapshot（周期/状态变更推送）和 event（单条业务事件）两类消息。

/** 大屏 ready_queue / in_process 共用的最小数据项 */
export interface DashboardPartItem {
  id: number
  serial_no: string | null
  name: string
  drawing_no: string
  quantity: number
  is_urgent: boolean
  planned_delivery_date: string | null
  released_at: string | null
  picked_up_at: string | null
  current_worker_id: number | null
  worker_name: string | null
  customer_name: string | null
  customer_path: string | null
}

/** 未来某一天需要交货的零件数（按 date 升序） */
export interface UpcomingDeliveryEntry {
  /** YYYY-MM-DD */
  date: string
  count: number
}

export interface DashboardSnapshotData {
  ready_queue: DashboardPartItem[]
  in_process: DashboardPartItem[]
  upcoming_delivery: UpcomingDeliveryEntry[]
  ts: string
}

export interface DashboardSnapshot {
  type: 'snapshot'
  data: DashboardSnapshotData
  ts: string
}

// ============================================================
// 业务事件消息（横幅通知消费）
// ============================================================

export type DashboardEventType = 'PICKED_UP' | 'RELEASED'

export interface DashboardEventPayload {
  serial_no: string | null
  drawing_no: string
  name: string
  customer_path: string | null
  is_urgent: boolean
  planned_delivery_date: string | null
  worker_name?: string | null
}

export interface DashboardEvent {
  type: 'event'
  event_type: DashboardEventType
  data: DashboardEventPayload
  ts: string
}

/** 服务端可能推送的两类消息联合类型（前端按 type 分发） */
export type DashboardServerMessage = DashboardSnapshot | DashboardEvent

export type ConnectionStatus = 'connecting' | 'open' | 'closed'