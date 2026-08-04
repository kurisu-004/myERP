/** 工种 (WorkType) — 工人所属的工种类别 */

export interface WorkType {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  code: string
  name: string
  description: string | null
  sort_order: number
  /** 2026-08-05：工种可领取上限（持有批次数）；null=不限 */
  max_held_batches: number | null
  created_at: string
  updated_at: string
}

export interface WorkTypeListResult {
  items: WorkType[]
  total: number
  limit: number
  offset: number
}

export interface WorkTypeCreatePayload {
  code: string
  name: string
  description?: string | null
  sort_order?: number
  max_held_batches?: number | null
}

export interface WorkTypeUpdatePayload {
  name?: string
  description?: string | null
  sort_order?: number
  max_held_batches?: number | null
}

export interface WorkTypeProcessLink {
  process_id: string
  process_code: string
  process_name: string
  sort_order: number
}

export interface WorkTypeWithProcesses extends WorkType {
  processes: WorkTypeProcessLink[]
}

export interface SetWorkTypeProcessesPayload {
  process_ids: string[]
}