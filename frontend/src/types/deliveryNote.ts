// 送货单管理（PR-G 2026-07-22 新增）.
// 形态对齐 frontend/src/types/parts.ts。

export type DeliveryNoteStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'PICKED_UP'
  | 'ARCHIVED'

export const DELIVERY_NOTE_STATUS_LABEL: Record<DeliveryNoteStatus, string> = {
  DRAFT: '草稿',
  SUBMITTED: '待送货',
  PICKED_UP: '已领取',
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
  status: string
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
  created_at: string
  updated_at: string
}

export interface DeliveryNoteDetailOut extends DeliveryNoteOut {
  line_items: DeliveryNoteLineItem[]
  scanned_serials: string[]
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
