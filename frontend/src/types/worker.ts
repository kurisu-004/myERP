export interface Worker {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  badge_code: string
  name: string
  work_type_id: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface WorkerListResult {
  items: Worker[]
  total: number
  limit: number
  offset: number
}

export interface WorkerCreatePayload {
  badge_code: string
  name: string
  work_type_id?: string | null
}

export interface WorkerUpdatePayload {
  name?: string
  badge_code?: string
  work_type_id?: string | null
}