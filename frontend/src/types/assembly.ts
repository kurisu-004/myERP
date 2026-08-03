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

/** 装配件状态枚举（与后端 model/enums.py::AssemblyStatus 对齐，2026-08-03 扩 7 态）。 */
export type AssemblyStatus =
  | 'PENDING'
  | 'IN_PROCESS'
  | 'INSPECTION'
  | 'READY_TO_SHIP'
  | 'DELIVERED'
  | 'COMPLETED'
  | 'CANCELLED'

/** 装配件状态 → 中文 label（与 PartsList 同款模式）。 */
export const ASSEMBLY_STATUS_LABEL: Record<AssemblyStatus, string> = {
  PENDING: '待生产',
  IN_PROCESS: '生产中',
  INSPECTION: '待品检',
  READY_TO_SHIP: '待送货',
  DELIVERED: '已送货',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

/** 装配件状态 → el-tag type（与 ORDER_STATUS_TAG_TYPE 视觉语义对齐）。 */
export const ASSEMBLY_STATUS_TAG_TYPE: Record<AssemblyStatus,
  'info' | 'warning' | 'success' | 'danger' | 'primary'> = {
  PENDING: 'info',
  IN_PROCESS: 'primary',
  INSPECTION: 'warning',
  READY_TO_SHIP: 'primary',
  DELIVERED: 'success',
  COMPLETED: 'success',
  CANCELLED: 'info',
}

/** 装配件（与后端 TAssembly 对齐） */
export interface AssemblyItem {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增（后端 SQLAlchemy version_id_col）；
   *  当前端暂不消费，后续可用于冲突检测。 */
  version: number
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
  /** PENDING / IN_PROCESS / INSPECTION / READY_TO_SHIP / DELIVERED / COMPLETED / CANCELLED */
  status: AssemblyStatus
  child_count: number
  // —— 2026-07-24 新增：装配体自身价格 + 送货单字段 ——
  /** 装配体套数（默认 1） */
  quantity: number
  /** 装配体单价（Decimal 序列化为 number） */
  unit_price: number
  /** 装配体总价 = quantity * unit_price（后端落库） */
  total_price: number
  /** 订单号（法拉/路达共用） */
  order_no: string | null
  /** 订单方系统内部交期 */
  system_delivery_date: string | null
  /** 备注 */
  note: string | null
  created_at: string
  updated_at: string
}

/** 列表窄出参（与 AssemblyItem 字段一致 + serial_no）。 */
export type AssemblyListItem = AssemblyItem

export interface AssemblyListQuery {
  /** 雪花 ID 字符串（CLAUDE.md §3 — 19 位 > JS Number.MAX_SAFE_INTEGER） */
  customer_id?: string
  status?: AssemblyStatus | string
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

/** 创建装配件时的子零件条目（PDF 按页拆分后，前端只需要填基础字段）。 */
export interface AssemblyChildPayload {
  drawing_no: string
  name: string
  quantity?: number
  applicant_name?: string | null
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
  /** 子件；可空（创建空装配体到详情页再补） */
  children?: AssemblyChildPayload[]
  // —— 2026-07-24 新增：装配体自身价格 + 送货单字段 ——
  quantity?: number
  unit_price?: number
  /** 不传时由 service 按 unit_price * quantity 计算 */
  total_price?: number | null
  order_no?: string | null
  system_delivery_date?: string | null
  note?: string | null
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
/** 编辑装配件的 payload（field-level partial；POST /assemblies/{id}/update）。 */
export interface AssemblyUpdatePayload {
  drawing_no?: string | null
  name?: string | null
  /** 雪花 ID 字符串（CLAUDE.md §3） */
  customer_id?: string | null
  applicant_name?: string | null
  /** 雪花 ID 字符串 */
  applicant_id?: string | null
  /** YYYY-MM-DD */
  request_date?: string | null
  planned_delivery_date?: string | null
  actual_delivery_date?: string | null
  is_urgent?: boolean | null
  // —— 2026-07-24 新增 ——
  quantity?: number | null
  unit_price?: number | null
  /** 显式传值时按 caller 写入；不传时按 unit_price * quantity 自动重算 */
  total_price?: number | null
  order_no?: string | null
  system_delivery_date?: string | null
  note?: string | null
}
