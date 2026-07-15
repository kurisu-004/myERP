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