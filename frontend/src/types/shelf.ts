export interface Shelf {
  id: string
  code: string
  name: string
  zone: string  // PRODUCTION | INSPECTION
  location: string | null
  is_active: boolean
  account_count: number
  created_at: string
  updated_at: string
}

export interface ShelfListResult {
  items: Shelf[]
  total: number
  limit: number
  offset: number
}

export interface ShelfProcessLink {
  process_id: string
  process_code: string
  process_name: string
  sort_order: number
}

export interface ShelfWithProcesses {
  id: string
  code: string
  name: string
  zone: string
  processes: ShelfProcessLink[]
}

export interface SetShelfProcessesPayload {
  process_ids: string[]
}
