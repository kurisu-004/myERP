// 送货单管理（PR-G 2026-07-22 新增；2026-07-23 R2-C 扩字段 + status 收紧）.
// 形态对齐 frontend/src/types/parts.ts。

import type { OrderStatus } from './parts'

export type DeliveryNoteStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'PICKED_UP'
  | 'ARCHIVED'

export const DELIVERY_NOTE_STATUS_LABEL: Record<DeliveryNoteStatus, string> = {
  DRAFT: '草稿',
  SUBMITTED: '待送货',
  // 2026-07-23：司机扫码送货后单据停在 PICKED_UP（不再自动 archive），展示为「已送货」
  PICKED_UP: '已送货',
  ARCHIVED: '已归档',
}

// Element Plus el-tag type 映射：未配置则降级为 plain info。
export const DELIVERY_NOTE_STATUS_TAG: Record<
  DeliveryNoteStatus,
  'info' | 'warning' | 'success' | 'danger' | ''
> = {
  DRAFT: 'info',
  SUBMITTED: 'warning',
  PICKED_UP: 'success',
  ARCHIVED: '',
}

// 一览 sort key
export type DeliveryNoteSortKey =
  | 'CREATED_AT'
  | 'SUBMITTED_AT'
  | 'PICKED_UP_AT'
  | 'DELIVERY_NOTE_NO'
export type DeliveryNoteSortDir = 'ASC' | 'DESC'

export interface DeliveryNoteLineItem {
  id: string
  serial_no: string
  drawing_no: string
  name: string
  quantity: number
  is_urgent: boolean
  /** 2026-07-23 R2-C：收紧为 OrderStatus 联合类型（与 PartsList 同源） */
  status: OrderStatus

  // 2026-07-23 R2-C：与 PartsList 列对齐，便于 XLSX 打印 / 详情可视
  applicant_name: string | null
  request_date: string | null
  planned_delivery_date: string | null
  system_delivery_date: string | null
  order_no: string | null
  note: string | null

  // 客户信息：part.customer_id 是 L2 叶子；与 note.customer_id=L1 root 不同
  customer_name: string | null
  parent_customer_name: string | null
  customer_path: string | null

  /** 已扫过 = true；前端两种命名都允许读。 */
  is_scanned: boolean
  scanned: boolean
}

export interface DeliveryNoteOut {
  id: string
  version: number
  delivery_note_no: string
  customer_id: string
  customer_name: string | null
  parent_customer_name: string | null
  customer_path: string | null
  status: DeliveryNoteStatus
  submitted_at: string | null
  picked_up_at: string | null
  submitted_by: string | null
  picked_up_by: string | null
  driver_worker_id: string | null
  driver_worker_name: string | null
  part_count: number
  note: string | null
  /** YYYY-MM-DD；创建时默认今天；DRAFT/SUBMITTED 可手动改 */
  delivery_date: string | null
  created_at: string
  updated_at: string
}

export interface DeliveryNoteDetailOut extends DeliveryNoteOut {
  line_items: DeliveryNoteLineItem[]
  scanned_serials: string[]
}

/**
 * 候选入单零件（同 L1 根、status ∈ {INSPECTION, READY_TO_SHIP}、
 * 不在 active 单上的件）。`PartPickerDialog` 用此类型勾选。
 */
export interface DeliveryNoteCandidatePart {
  id: string
  serial_no: string
  drawing_no: string
  name: string
  quantity: number
  applicant_name: string | null
  /** INSPECTION 待检 / READY_TO_SHIP 已通过品检 */
  status: 'INSPECTION' | 'READY_TO_SHIP' | string
  planned_delivery_date: string | null
}

export interface DeliveryNoteEventOut {
  id: string
  delivery_note_id: string
  event_type:
    | 'CREATED'
    | 'EDITED'
    | 'ITEM_ADDED'
    | 'ITEM_REMOVED'
    | 'SUBMITTED'
    | 'RECALLED'
    | 'PICKUP_SCANNED'
    | 'PICKED_UP'
    | 'ARCHIVED'
  from_status: string | null
  to_status: string | null
  drawing_code: string | null
  badge_code: string | null
  note: string | null
  scanned_count: number | null
  expected_count: number | null
  created_by: string | null
  created_at: string | null
}

export interface DeliveryNotePickupScanOut {
  delivery_note_id: string
  scanned_count: number
  expected_count: number
  ready: boolean
  scanned_serials: string[]
}
