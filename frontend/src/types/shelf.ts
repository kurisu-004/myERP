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
