// 大屏 WebSocket 数据类型。snapshot（周期/状态变更）和 event（单条业务事件）两类。

export interface DashboardPartItem {
  id: string
  serial_no: string | null
  name: string
  drawing_no: string
  quantity: number
  is_urgent: boolean
  planned_delivery_date: string | null
  picked_up_at: string | null
  current_holder_id: string | null
  current_holder_kind: 'shelf' | 'worker' | null
  shelf_code: string | null
  placed_at: string | null
  worker_name?: string | null
  customer_name: string | null
  customer_path: string | null
}

export interface DashboardShelfGroup {
  shelf_id: string
  shelf_code: string
  shelf_name: string
  items: DashboardPartItem[]
}

export interface UpcomingDeliveryEntry {
  date: string
  count: number
}

export interface DashboardSnapshotData {
  on_production_shelves: DashboardShelfGroup[]
  on_inspection_shelves: DashboardPartItem[]
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

export type DashboardEventType =
  | 'PICKED_UP'
  | 'RELEASED'
  | 'PLACED_ON_SHELF'
  | 'RETURNED'
  | 'INSPECTED'
  | 'ASSEMBLY_CANCELLED'
  | 'ASSEMBLY_DELETED'

export interface DashboardEventPayload {
  // 零件事件携带的字段（ASSEMBLY_* 不带这些）
  serial_no?: string | null
  drawing_no?: string | null
  name?: string | null
  customer_path?: string | null
  is_urgent?: boolean | null
  planned_delivery_date?: string | null
  worker_name?: string | null
  shelf_code?: string | null
  // 装配体事件携带的字段
  assembly_id?: string | null
}

export interface DashboardEvent {
  type: 'event'
  event_type: DashboardEventType
  data: DashboardEventPayload
  ts: string
}

export type DashboardServerMessage = DashboardSnapshot | DashboardEvent
export type ConnectionStatus = 'connecting' | 'open' | 'closed'
