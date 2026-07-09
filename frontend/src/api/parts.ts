// 后端零件 API 封装（走 @/api/http 统一 axios 客户端）。
// 所有 ID 在前端是字符串（雪花 ID 经后端 IdStr 序列化）。

import { api } from '@/api/http'
import type {
  OrderStatus,
  PartEventType,
  PartListItem,
  PartSortKey,
  SortDir,
} from '@/types/parts'

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
  /** 下一道工序 id（NULL = 未设置） */
  next_process_id: string | null
  /** 下一道工序名称（NULL = 未设置；由后端在 list/get 响应中带出） */
  next_process_name: string | null
}

export interface PartListResult {
  items: PartListItem[]
  total: number
  limit: number
  offset: number
}

/** 雪花 ID 字符串（CLAUDE.md §3 — 19 位 > JS Number.MAX_SAFE_INTEGER） */
export interface ListPartsParams {
  customer_id?: string
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
  /**
   * 申请人表 id（雪花 ID 字符串）。
   * 必须是字符串：雪花 ID 19 位 > JS Number.MAX_SAFE_INTEGER（2^53-1），
   * 用 number 类型会在 JSON 序列化时丢精度，后端拿不到原值。
   */
  applicant_id?: string | null
  quantity?: number
  unit_price?: number
  total_price?: number | null
  request_date: string
  planned_delivery_date: string
  actual_delivery_date?: string | null
  is_urgent?: boolean
  /** 雪花 ID 字符串（CLAUDE.md §3） */
  customer_id: string
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
  /** 雪花 ID 字符串（CLAUDE.md §3） */
  customer_id?: string
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
  /** 仅 RETURNED 需要；工人指定的下一道工序 id */
  next_process_id?: string | null
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

export interface PlaceOnShelfPayload {
  shelf_id: string
  /** 下一道工序 id（必填） */
  next_process_id: string
}

export async function placeOnShelf(
  id: number | string,
  shelfId: string,
  nextProcessId: string,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/place-on-shelf`, {
    shelf_id: shelfId,
    next_process_id: nextProcessId,
  })
  return resp.data
}

/** PENDING → PROGRAMMING：文员把零件发送至 CNC 编程。 */
export async function sendToProgramming(id: number | string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/send-to-programming`)
  return resp.data
}

/** PROGRAMMING → IN_PROCESS：编程员上传完 G 代码后下发到生产货架。 */
export async function releaseFromProgramming(
  id: number | string,
  shelfId: string,
  nextProcessId: string,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${id}/release-from-programming`,
    { shelf_id: shelfId, next_process_id: nextProcessId },
  )
  return resp.data
}

/** 待编程一览：status=PROGRAMMING 的零件列表。 */
export async function listPendingProgramming(
  params: Omit<ListPartsParams, 'statuses' | 'is_urgent'> = {},
): Promise<PartListResult> {
  const resp = await api.get<PartListResult>(
    '/parts/pending-programming',
    { params: cleanParams(params) },
  )
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

export interface PartBatchFilePayload {
  /** 浏览器里的 File 对象（el-upload 的 uploadFile.raw）。 */
  data: Blob
  /** 原始文件名（含扩展名，后端据此判 PDF 类型）。 */
  filename: string
  /** 可选 content-type；后端会按文件扩展名兜底。 */
  contentType?: string
}

/**
 * 批量新建零件（multipart/form-data）。
 *
 * 后端 `POST /parts/batch` 2026-07-09 起接受 `data` (JSON 字符串) + `files` (PDF 数组)，
 * 文件与 items 按下标对齐；缺失位按无图处理。任一上传失败 → 整批回滚。
 *
 * 不手动设 Content-Type —— axios 会自动加正确的 multipart boundary。
 */
export async function batchCreateParts(
  items: PartCreatePayload[],
  files: (PartBatchFilePayload | null)[] = [],
): Promise<PartBatchResult> {
  const form = new FormData()
  form.append('data', JSON.stringify({ items }))
  files.forEach((f) => {
    if (f) form.append('files', f.data, f.filename)
  })
  const resp = await api.post<PartBatchResult>('/parts/batch', form)
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

/**
 * 扫码台 PICK_UP 列表：列出指定工种在指定货架上可领的零件。
 * 排序：加急优先 → 临期优先 → id 降序。
 */
export async function listPartsByWorkType(
  workTypeId: string,
  shelfId: string,
): Promise<PartItem[]> {
  const resp = await api.get<PartItem[]>(
    `/parts/by-work-type/${encodeURIComponent(workTypeId)}`,
    { params: { shelf_id: shelfId } },
  )
  return resp.data
}

/**
 * 共享 HMI PICK_UP 跨架列表（2026-07-10）。
 * 列出**所有**生产货架上、该工种可领的零件；前端按 `current_holder_id`
 * 在卡片网格里分组。
 * 排序与 `listPartsByWorkType` 一致。
 */
export async function listPartsByWorkTypeAllShelves(
  workTypeId: string,
): Promise<PartItem[]> {
  const resp = await api.get<PartItem[]>(
    `/parts/pickable-by-work-type/${encodeURIComponent(workTypeId)}`,
  )
  return resp.data
}

/**
 * 生成零件的双面打印 PDF（图纸 + 反面右下角条形码）。
 * 返回 Blob，content-type=application/pdf。
 *
 * 注：返回的是文件 blob，调用方需自行用 iframe / window 触发打印。
 */
export async function printPartDrawing(partId: string): Promise<Blob> {
  const resp = await api.get<Blob>(
    `/parts/${encodeURIComponent(partId)}/print-drawing`,
    { responseType: 'blob' },
  )
  return resp.data
}