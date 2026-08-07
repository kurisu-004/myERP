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
  /** 批次 id（行身份；2026-07-29 批次化） */
  id: string
  /** 工单 id */
  part_id: string
  batch_no: number | null
  batch_label: string | null
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

  // 2026-08-04：装配件父行字段（仅子件行填；散件为 null）。前端详情页用它构造
  // 可折叠的装配件父行，打印预览用它做「合并为一套」分组。
  assembly_id: string | null
  assembly_serial_no: string | null
  assembly_drawing_no: string | null
  assembly_name: string | null
  assembly_order_no: string | null
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
  /** 工单 id */
  id: string
  /** 批次 id（入单回传用；2026-07-29 批次化） */
  batch_id: string
  batch_no: number | null
  batch_label: string | null
  serial_no: string
  drawing_no: string
  name: string
  quantity: number
  applicant_name: string | null
  /** INSPECTION 待检 / READY_TO_SHIP 已通过品检 */
  status: 'INSPECTION' | 'READY_TO_SHIP' | string
  planned_delivery_date: string | null
  /** 2026-08-01：订单号（picker 新增列与排序） */
  order_no: string | null
  // —— 2026-08-07 picker 富化 ——
  /** 零件所属二级（L2）客户名 */
  customer_name: string | null
  /** 所属一级（L1 root）客户名 */
  parent_customer_name: string | null
  /** L1 / L2 路径（与 note.customer_path 同格式） */
  customer_path: string | null
}

// 2026-07-23 Bug 4：精简为 4 类事件 + RECALLED（历史只读）
// WITHDRAWN 替代 RECALLED 作为新写入值；RECALLED 仅出现在已部署库的旧行。
// 详情页时间线统一通过 DELIVERY_NOTE_EVENT_TYPE_LABEL 翻译为中文。
export type DeliveryNoteEventTypeName =
  | 'CREATED'
  | 'SUBMITTED'
  | 'WITHDRAWN'
  | 'RECALLED' // 历史只读
  | 'PICKED_UP'

export const DELIVERY_NOTE_EVENT_TYPE_LABEL: Record<string, string> = {
  CREATED: '创建',
  SUBMITTED: '提交',
  WITHDRAWN: '撤回',
  RECALLED: '撤回', // 历史事件兜底；新代码不会再写此值
  PICKED_UP: '领取',
}

/** 未命中 DELIVERY_NOTE_EVENT_TYPE_LABEL 的事件保留原始值便于排查。 */
export function formatNoteEventLabel(eventType: string): string {
  return DELIVERY_NOTE_EVENT_TYPE_LABEL[eventType] ?? `未知事件（${eventType}）`
}

export interface DeliveryNoteEventOut {
  id: string
  delivery_note_id: string
  event_type: DeliveryNoteEventTypeName | string
  from_status: string | null
  to_status: string | null
  note: string | null
  created_by: string | null
  created_at: string | null
}

export interface DeliveryNotePickupScanOut {
  delivery_note_id: string
  /** 2026-07-23 改：后端不再维护扫码进度；恒为 0。前端基于本地 Set 判 ready。 */
  scanned_count: number
  expected_count: number
  /** 2026-07-23 改：后端无法判定 ready（无状态数据源）；恒为 false。 */
  ready: boolean
  /** 2026-07-23 改：恒为空；前端不应据此覆盖本地状态。 */
  scanned_serials: string[]
}
