/** 工序 (Process) — 零件的加工步骤 */

export type ProcessCategory = 'INHOUSE' | 'OUTSOURCE'

export const PROCESS_CATEGORY_LABEL: Record<ProcessCategory, string> = {
  INHOUSE: '自产',
  OUTSOURCE: '外协',
}

export interface Process {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  code: string
  name: string
  category: ProcessCategory
  sort_order: number
  description: string | null
  created_at: string
  updated_at: string
}

export interface ProcessListResult {
  items: Process[]
  total: number
  limit: number
  offset: number
}

export interface ProcessCreatePayload {
  code: string
  name: string
  category: ProcessCategory
  sort_order?: number
  description?: string | null
}

export interface ProcessUpdatePayload {
  name?: string
  category?: ProcessCategory
  sort_order?: number
  description?: string | null
}