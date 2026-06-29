// 后端零件 API 封装（fetch）。统一处理 R<T> 解包。

import type { OrderStatus, PartEventType, PartSortKey, SortDir } from '@/types/parts'

/** 后端 PartOut 接口（与 Pydantic schema PartOut 对齐） */
export interface PartItem {
  id: number
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
}

export interface PartListResult {
  items: PartItem[]
  total: number
  limit: number
  offset: number
}

export interface ListPartsParams {
  customer_id?: number
  status?: OrderStatus
  is_urgent?: boolean
  drawing_no_like?: string
  name_like?: string
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

export interface PartPickUpPayload {
  drawing_code: string
  badge_code: string
}

export interface PartScanPayload {
  drawing_code: string
  event_type: PartEventType
}

export interface PartEvent {
  id: number
  part_id: number
  worker_id: number | null
  worker_name: string | null
  event_type: string
  from_status: string | null
  to_status: string | null
  drawing_code: string | null
  badge_code: string | null
  note: string | null
  created_at: string
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

async function unwrap<T>(resp: Response): Promise<T> {
  const json = (await resp.json()) as ApiEnvelope<T>
  if (json.code !== 0) {
    throw new Error(json.message || `API error code=${json.code}`)
  }
  return json.data
}

export async function listParts(
  params: ListPartsParams = {},
): Promise<PartListResult> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    qs.set(k, String(v))
  }
  const url = `/api/v1/parts${qs.toString() ? '?' + qs.toString() : ''}`
  const resp = await fetch(url)
  return unwrap<PartListResult>(resp)
}

export async function getPart(id: number): Promise<PartItem> {
  const resp = await fetch(`/api/v1/parts/${id}`)
  return unwrap<PartItem>(resp)
}

export async function createPart(payload: PartCreatePayload): Promise<PartItem> {
  const resp = await fetch('/api/v1/parts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return unwrap<PartItem>(resp)
}

export async function changePartStatus(
  id: number,
  payload: PartStatusChangePayload,
): Promise<PartItem> {
  const resp = await fetch(`/api/v1/parts/${id}/change-status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return unwrap<PartItem>(resp)
}

export async function releasePart(id: number): Promise<PartItem> {
  const resp = await fetch(`/api/v1/parts/${id}/release`, { method: 'POST' })
  return unwrap<PartItem>(resp)
}

export async function pickUpPart(payload: PartPickUpPayload): Promise<PartItem> {
  const resp = await fetch('/api/v1/parts/pick-up', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return unwrap<PartItem>(resp)
}

export async function scanPart(payload: PartScanPayload): Promise<PartItem> {
  const resp = await fetch('/api/v1/parts/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return unwrap<PartItem>(resp)
}

export async function listPartEvents(id: number): Promise<PartEvent[]> {
  const resp = await fetch(`/api/v1/parts/${id}/events`)
  return unwrap<PartEvent[]>(resp)
}

export interface PartBatchFailure {
  index: number
  message: string
}

export interface PartBatchResult {
  created: PartItem[]
  failed: PartBatchFailure[]
}

export async function batchCreateParts(
  items: PartCreatePayload[],
): Promise<PartBatchResult> {
  const resp = await fetch('/api/v1/parts/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
  return unwrap<PartBatchResult>(resp)
}

export async function softDeletePart(id: number): Promise<void> {
  const resp = await fetch(`/api/v1/parts/${id}/soft-delete`, { method: 'POST' })
  await unwrap<{ ok: boolean }>(resp)
}

export async function getPartBySerial(serialNo: string): Promise<PartItem> {
  const resp = await fetch(`/api/v1/parts/by-serial/${encodeURIComponent(serialNo)}`)
  return unwrap<PartItem>(resp)
}