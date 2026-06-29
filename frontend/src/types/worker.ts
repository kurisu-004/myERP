export interface Worker {
  id: number
  badge_code: string
  name: string
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
}

export interface WorkerUpdatePayload {
  name?: string
  badge_code?: string
}