/** 外协公司 (OutsourceCompany) — 工序能力清单 / CRUD */

import type { ProcessCategory } from './process'

/** 单条外协公司（无映射） */
export interface OutsourceCompany {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  name: string
  contact_name: string | null
  contact_phone: string | null
  address: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

/** 单条映射条目 */
export interface OutsourceCompanyProcessLink {
  process_id: string
  process_code: string
  process_name: string
  category: ProcessCategory
  sort_order: number
}

/** 公司 + 映射的全部工序 */
export interface OutsourceCompanyWithProcesses extends OutsourceCompany {
  processes: OutsourceCompanyProcessLink[]
}

export interface OutsourceCompanyListResult {
  items: OutsourceCompany[]
  total: number
  limit: number
  offset: number
}

export interface OutsourceCompanyCreatePayload {
  name: string
  contact_name?: string | null
  contact_phone?: string | null
  address?: string | null
  is_active?: boolean
  /** 创建时可一并提交 OUTSOURCE 工序 id 列表（雪花 ID 字符串，提交顺序即 sort_order） */
  process_ids?: string[]
}

export interface OutsourceCompanyUpdatePayload {
  name?: string
  contact_name?: string | null
  contact_phone?: string | null
  address?: string | null
  is_active?: boolean
}

export interface SetOutsourceCompanyProcessesPayload {
  /** 雪花 ID 字符串（前端 Number() 会丢精度，必须 str） */
  process_ids: string[]
}


// ============================================================
// 外协报价 (OutsourceQuote) — 2026-07-16 新增
// ============================================================

/** 报价状态枚举 */
export type OutsourceQuoteStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'APPROVED'
  | 'REJECTED'
  | 'USED'

export const OUTSOURCE_QUOTE_STATUS_LABEL: Record<OutsourceQuoteStatus, string> = {
  DRAFT: '草稿',
  SUBMITTED: '待审核',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
  USED: '已使用',
}

export const OUTSOURCE_QUOTE_STATUS_TAG: Record<OutsourceQuoteStatus, 'info' | 'warning' | 'success' | 'danger' | ''> = {
  DRAFT: 'info',
  SUBMITTED: 'warning',
  APPROVED: 'success',
  REJECTED: 'danger',
  USED: '',
}

/** 单条外协报价（service 层已注入预解析字段） */
export interface OutsourceQuote {
  id: string
  /** 乐观锁版本号；update / approve / reject 入参必填 */
  version: number
  part_id: string
  outsource_company_id: string
  process_id: string
  /** Decimal 后端序列化为字符串 */
  price: string
  note: string | null
  status: OutsourceQuoteStatus
  submitted_at: string | null
  reviewed_at: string | null
  review_note: string | null
  created_at: string
  updated_at: string
  // 预解析字段
  part_serial_no: string | null
  part_drawing_no: string | null
  part_name: string | null
  outsource_company_name: string | null
  process_code: string | null
  process_name: string | null
  customer_path: string | null
}

export interface OutsourceQuoteListResult {
  items: OutsourceQuote[]
  total: number
  limit: number
  offset: number
}

export interface OutsourceQuoteCreatePayload {
  part_id: string
  outsource_company_id: string
  process_id: string
  price: string
  note?: string | null
}

export interface OutsourceQuoteUpdatePayload {
  version: number
  price?: string
  note?: string | null
}

export interface OutsourceQuoteApprovePayload {
  version: number
  review_note?: string | null
}

export interface OutsourceQuoteRejectPayload {
  version: number
  review_note: string
}

/** 「外协发送」列表（ApprovedQuoteForSendItem） */
export interface ApprovedQuoteForSendItem {
  part_id: string
  part_serial_no: string | null
  part_drawing_no: string | null
  part_name: string | null
  quantity: number | null
  planned_delivery_date: string | null
  is_urgent: boolean
  customer_path: string | null
  next_process_id: string | null
  next_process_name: string | null
  outsource_company_id: string
  outsource_company_name: string | null
  process_id: string
  process_name: string | null
  price: string
  status_label: 'sendable'
}

export interface ApprovedForSendListResult {
  items: ApprovedQuoteForSendItem[]
  total: number
  limit: number
  offset: number
}