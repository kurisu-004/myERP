export interface Shelf {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  code: string
  name: string
  zone: string  // PRODUCTION | INSPECTION
  location: string | null
  is_active: boolean
  account_count: number
  /**
   * 物理顺序（0=未设置；manager 在 ShelfList 后台手填）。
   * 共享 HMI 卡片网格 picker 按 (display_order ASC, code ASC) 排。
   */
  display_order: number
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

// ============================================================
// 共享 HMI RETURN 卡片网格 picker（2026-07-10）
// ============================================================
export interface ShelfForReturn {
  id: string
  code: string
  name: string
  location: string | null
  display_order: number
  /** 当前在架件数（status=IN_PROCESS + holder=shelf） */
  current_load: number
  /** 已映射工序的 code 列表（前端 chips 展示） */
  mapped_process_codes: string[]
  /** 系统推荐标记；picker 弹窗时默认高亮 + 「完成」一键接受 */
  is_recommended: boolean
}

export interface ShelfForReturnResult {
  items: ShelfForReturn[]
  recommended_shelf_id: string
}
