// 送货单管理 API 封装（PR-G 2026-07-22 重写）。
// 全部雪花 ID 入参为 string（CLAUDE.md §3 JS Number 丢精度）。
//
// 端点清单（与 api/v1/delivery_note.py 对应）：
//   GET    /delivery-notes                       - listNotes
//   GET    /delivery-notes/pickup-pending        - listPickupPending
//   POST   /delivery-notes                       - createNote
//   GET    /delivery-notes/{id}                  - getNote
//   GET    /delivery-notes/{id}/events           - listNoteEvents
//   POST   /delivery-notes/{id}/add-parts        - addParts
//   POST   /delivery-notes/{id}/remove-parts     - removeParts
//   POST   /delivery-notes/{id}/submit          - submitNote
//   POST   /delivery-notes/{id}/recall           - recallNote
//   POST   /delivery-notes/{id}/pickup-scan      - pickupScan
//   POST   /delivery-notes/{id}/pickup           - pickup
//   POST   /delivery-notes/{id}/soft-delete      - softDelete

import { api } from '@/api/http'
import type {
  DeliveryNoteDetailOut,
  DeliveryNoteEventOut,
  DeliveryNoteOut,
  DeliveryNotePickupScanOut,
  DeliveryNoteSortDir,
  DeliveryNoteSortKey,
  DeliveryNoteStatus,
} from '@/types/deliveryNote'

export interface ListNotesParams {
  statuses?: DeliveryNoteStatus[]
  customer_id?: string
  keyword?: string
  sort_by?: DeliveryNoteSortKey
  sort_dir?: DeliveryNoteSortDir
  limit?: number
  offset?: number
}

export interface DeliveryNoteListResponse {
  items: DeliveryNoteOut[]
  total: number
  limit: number
  offset: number
}

export interface CreateNotePayload {
  customer_id: string
  note?: string | null
}

export interface PartIdsPayload {
  part_ids: string[]
  version: number
}

export interface VersionPayload {
  version: number
}

export interface PickupScanPayload {
  part_serial: string
  badge_code?: string | null
}

export interface PickupPayload {
  driver_worker_id: string
  version: number
  badge_code?: string | null
}

// 1) list
export async function listNotes(
  params: ListNotesParams = {},
): Promise<DeliveryNoteListResponse> {
  const query: Record<string, unknown> = {}
  if (params.statuses?.length) query.statuses = params.statuses
  if (params.customer_id) query.customer_id = params.customer_id
  if (params.keyword) query.keyword = params.keyword
  if (params.sort_by) query.sort_by = params.sort_by
  if (params.sort_dir) query.sort_dir = params.sort_dir
  if (params.limit !== undefined) query.limit = params.limit
  if (params.offset !== undefined) query.offset = params.offset
  const resp = await api.get<DeliveryNoteListResponse>('/delivery-notes', {
    params: query,
  })
  return resp.data
}

// 2) pickup-pending list
export async function listPickupPending(
  customer_id?: string,
): Promise<DeliveryNoteOut[]> {
  const resp = await api.get<{ items: DeliveryNoteOut[] }>(
    '/delivery-notes/pickup-pending',
    { params: customer_id ? { customer_id } : {} },
  )
  return resp.data.items
}

// 3) create draft
export async function createNote(
  payload: CreateNotePayload,
): Promise<DeliveryNoteOut> {
  const resp = await api.post<DeliveryNoteOut>('/delivery-notes', payload)
  return resp.data
}

// 4) detail
export async function getNote(noteId: string): Promise<DeliveryNoteDetailOut> {
  const resp = await api.get<DeliveryNoteDetailOut>(`/delivery-notes/${noteId}`)
  return resp.data
}

// 5) events
export async function listNoteEvents(
  noteId: string,
): Promise<DeliveryNoteEventOut[]> {
  const resp = await api.get<DeliveryNoteEventOut[]>(
    `/delivery-notes/${noteId}/events`,
  )
  return resp.data
}

// 6) add-parts
export async function addParts(
  noteId: string,
  payload: PartIdsPayload,
): Promise<DeliveryNoteDetailOut> {
  const resp = await api.post<DeliveryNoteDetailOut>(
    `/delivery-notes/${noteId}/add-parts`,
    payload,
  )
  return resp.data
}

// 7) remove-parts
export async function removeParts(
  noteId: string,
  payload: PartIdsPayload,
): Promise<DeliveryNoteDetailOut> {
  const resp = await api.post<DeliveryNoteDetailOut>(
    `/delivery-notes/${noteId}/remove-parts`,
    payload,
  )
  return resp.data
}

// 8) submit
export async function submitNote(
  noteId: string,
  payload: VersionPayload,
): Promise<DeliveryNoteOut> {
  const resp = await api.post<DeliveryNoteOut>(
    `/delivery-notes/${noteId}/submit`,
    payload,
  )
  return resp.data
}

// 9) recall
export async function recallNote(
  noteId: string,
  payload: VersionPayload,
): Promise<DeliveryNoteOut> {
  const resp = await api.post<DeliveryNoteOut>(
    `/delivery-notes/${noteId}/recall`,
    payload,
  )
  return resp.data
}

// 10) pickup-scan (driver 累积扫描)
export async function pickupScan(
  noteId: string,
  payload: PickupScanPayload,
): Promise<DeliveryNotePickupScanOut> {
  const resp = await api.post<DeliveryNotePickupScanOut>(
    `/delivery-notes/${noteId}/pickup-scan`,
    payload,
  )
  return resp.data
}

// 11) pickup (finalize)
export async function pickup(
  noteId: string,
  payload: PickupPayload,
): Promise<DeliveryNoteOut> {
  const resp = await api.post<DeliveryNoteOut>(
    `/delivery-notes/${noteId}/pickup`,
    payload,
  )
  return resp.data
}

// 12) soft-delete
export async function softDeleteNote(
  noteId: string,
  payload: VersionPayload,
): Promise<void> {
  await api.post(`/delivery-notes/${noteId}/soft-delete`, payload)
}
