/** 工种 (WorkType) — 工人所属的工种类别 */

export interface WorkType {
  id: string
  code: string
  name: string
  description: string | null
  sort_order: number
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
}

export interface WorkTypeUpdatePayload {
  name?: string
  description?: string | null
  sort_order?: number
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