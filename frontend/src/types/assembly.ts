// types/assembly.ts
//
// 与后端 schema/assembly.py 对齐的 TypeScript 类型。

import type { PartListItem } from '@/types/parts'
import type { DrawingFileItem } from './file'

export type AssemblySortKey =
  | 'PLANNED_DELIVERY_DATE'
  | 'REQUEST_DATE'
  | 'CREATED_AT'
  | 'SERIAL_NO'
  | 'DRAWING_NO'
  | 'NAME'

export type SortDir = 'ASC' | 'DESC'

/** 装配件（与后端 TAssembly 对齐） */
export interface AssemblyItem {
  id: string
  /** 装配件流水号；老装配件为 null */
  serial_no: string | null
  drawing_no: string
  name: string
  applicant_name: string | null
  customer_id: string
  customer_name: string | null
  parent_customer_name: string | null
  customer_path: string | null
  request_date: string
  planned_delivery_date: string
  actual_delivery_date: string | null
  is_urgent: boolean
  /** PENDING / IN_PROCESS / COMPLETED / CANCELLED */
  status: string
  child_count: number
  created_at: string
  updated_at: string
}

/** 列表窄出参（与 AssemblyItem 字段一致 + serial_no）。 */
export type AssemblyListItem = AssemblyItem

export interface AssemblyListQuery {
  /** 雪花 ID 字符串（CLAUDE.md §3 — 19 位 > JS Number.MAX_SAFE_INTEGER） */
  customer_id?: string
  status?: string
  is_urgent?: boolean
  drawing_no_like?: string
  name_like?: string
  sort_by?: AssemblySortKey
  sort_dir?: SortDir
  limit?: number
  offset?: number
}

export interface AssemblyListResult {
  items: AssemblyListItem[]
  total: number
  limit: number
  offset: number
}

/** 创建装配件时的子零件条目 */
export interface AssemblyChildPayload {
  drawing_no: string
  name: string
  quantity?: number
  unit_price?: number
  total_price?: number | null
  applicant_name?: string | null
  /** 子件在 PDF 中的页码（page 1 是总装图，子件从 2 开始） */
  page_index: number
}

/** 创建装配件的 JSON body（不含文件；文件单独 multipart 传） */
export interface AssemblyCreatePayload {
  name: string
  drawing_no: string
  applicant_name?: string | null
  /**
   * 申请人表 id（雪花 ID 字符串）。必须是字符串：
   * 同 parts.ts 的 PartCreatePayload.applicant_id，详见 CLAUDE.md「雪花 ID 溢出」一节。
   */
  applicant_id?: string | null
  /** 雪花 ID 字符串（CLAUDE.md §3） */
  customer_id: string
  request_date: string
  planned_delivery_date: string
  is_urgent?: boolean
  children: AssemblyChildPayload[]
}

/** 创建结果（创建响应需要完整数据；子件用 PartListItem 即可，详情页用窄版） */
export interface AssemblyCreateResult {
  assembly: AssemblyItem
  children: PartListItem[]
  files: DrawingFileItem[]
}

/** 装配件详情：自身 + 子件 + 文件 */
export interface AssemblyDetail {
  assembly: AssemblyItem
  children: PartListItem[]
  files: DrawingFileItem[]
}