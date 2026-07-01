// types/assembly.ts
//
// 与后端 schema/assembly.py 对齐的 TypeScript 类型。

/** 装配件（与后端 TAssembly 对齐） */
export interface AssemblyItem {
  id: string
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
  /** PENDING / COMPLETED */
  status: string
  child_count: number
  created_at: string
  updated_at: string
}

export interface AssemblyListQuery {
  customer_id?: number
  status?: string
  is_urgent?: boolean
  drawing_no_like?: string
  name_like?: string
  limit?: number
  offset?: number
}

export interface AssemblyListResult {
  items: AssemblyItem[]
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
  customer_id: number
  request_date: string
  planned_delivery_date: string
  is_urgent?: boolean
  children: AssemblyChildPayload[]
}

import type { PartItem } from '@/api/parts'
import type { DrawingFileItem } from './file'

/** 创建结果 */
export interface AssemblyCreateResult {
  assembly: AssemblyItem
  children: PartItem[]
  files: DrawingFileItem[]
}

/** 装配件详情：自身 + 子件 + 文件 */
export interface AssemblyDetail {
  assembly: AssemblyItem
  children: PartItem[]
  files: DrawingFileItem[]
}