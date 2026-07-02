// 后端零件 API 封装（走 @/api/http 统一 axios 客户端）。
// 所有 ID 在前端是字符串（雪花 ID 经后端 IdStr 序列化）。

import { api } from '@/api/http'
import type { OrderStatus, PartEventType, PartSortKey, SortDir } from '@/types/parts'

export interface PartItem {
  id: string
  serial_no: string | null
  name: string
  drawing_no: string
  quantity: number
  planned_delivery_date: string
  actual_delivery_date: string | null
  is_urgent: boolean
  status: OrderStatus
  customer_name: string | null
  parent_customer_name: string | null
  customer_path: string | null
  assembly_id: string | null
  current_holder_id: string | null
  current_holder_kind: 'shelf' | 'worker' | null
  shelf_code: string | null
  worker_name: string | null
  location: string | null
  placed_at: string | null
}

export interface PartListResult {
  items: PartItem[]
  total: number
  limit: number
  offset: number
}

/** 入参侧 customer_id 仍按数字（数据库 BigInteger），由前端在调用前 Number() 转。 */
export interface ListPartsParams {
  customer_id?: number
  statuses?: OrderStatus[]
  is_urgent?: boolean
  keyword?: string
  sort_by?: PartSortKey
  sort_dir?: SortDir
  limit?: number
  offset?: number
}

export interface PartCreatePayload {
  name: string
  drawing_no: string
  applicant_name?: string
  quantity?: number
  unit_price?: number
  total_price?: number | null
  request_date: string
  planned_delivery_date: string
  actual_delivery_date?: string | null
  is_urgent?: boolean
  customer_id: number
}

export interface PartStatusChangePayload {
  status: OrderStatus
}

export interface PartUpdatePayload {
  name?: string
  drawing_no?: string
  applicant_name?: string
  quantity?: number
  unit_price?: number
  total_price?: number | null
  request_date?: string
  planned_delivery_date?: string
  actual_delivery_date?: string | null
  is_urgent?: boolean
  customer_id?: number
}

export interface PartPickUpPayload {
  serial_no: string
  shelf_id: string
  badge_code: string
}

export interface PartScanPayload {
  serial_no: string
  event_type: PartEventType
  shelf_id: string
  badge_code: string
  target_inspection_shelf_id?: string | null
}

export interface PartEvent {
  id: string
  part_id: string
  worker_id: string | null
  worker_name: string | null
  event_type: string
  from_status: string | null
  to_status: string | null
  drawing_code: string | null
  badge_code: string | null
  note: string | null
  created_at: string
}

export interface PartBatchFailure {
  index: number
  message: string
}

export interface PartBatchResult {
  created: PartItem[]
  failed: PartBatchFailure[]
}

// axios 会自动丢掉 undefined/null；但空串不会丢（会触发 LIKE '%%'）。
// 这里显式 filter 一下，确保空字符串参数也跳过。
function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

export async function listParts(
  params: ListPartsParams = {},
): Promise<PartListResult> {
  const resp = await api.get<PartListResult>('/parts', { params: cleanParams(params) })
  return resp.data
}

export async function getPart(id: string): Promise<PartItem> {
  const resp = await api.get<PartItem>(`/parts/${id}`)
  return resp.data
}

export async function createPart(payload: PartCreatePayload): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts', payload)
  return resp.data
}

export async function changePartStatus(
  id: string,
  payload: PartStatusChangePayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/change-status`, payload)
  return resp.data
}

export async function placeOnShelf(
  id: number | string,
  shelfId: string,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/place-on-shelf`, { shelf_id: shelfId })
  return resp.data
}

export async function pickUpPart(payload: PartPickUpPayload): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts/pick-up', payload)
  return resp.data
}

export async function scanPart(payload: PartScanPayload): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts/scan', payload)
  return resp.data
}

export async function listPartEvents(id: string): Promise<PartEvent[]> {
  const resp = await api.get<PartEvent[]>(`/parts/${id}/events`)
  return resp.data
}

export async function batchCreateParts(
  items: PartCreatePayload[],
): Promise<PartBatchResult> {
  const resp = await api.post<PartBatchResult>('/parts/batch', { items })
  return resp.data
}

export async function softDeletePart(id: string): Promise<void> {
  await api.post(`/parts/${id}/soft-delete`)
}

export async function updatePart(
  id: string,
  payload: PartUpdatePayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/update`, payload)
  return resp.data
}

export async function cancelPart(id: string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/cancel`)
  return resp.data
}

export async function getPartBySerial(serialNo: string): Promise<PartItem> {
  const resp = await api.get<PartItem>(
    `/parts/by-serial/${encodeURIComponent(serialNo)}`,
  )
  return resp.data
}