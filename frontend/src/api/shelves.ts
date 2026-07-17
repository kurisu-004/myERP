// 货架 API（走 @/api/http 统一 axios 客户端）。

import { api } from '@/api/http'
import type {
  Shelf,
  ShelfForReturnResult,
  ShelfListResult,
  ShelfWithProcesses,
  SetShelfProcessesPayload,
} from '@/types/shelf'

export interface ListShelvesParams {
  zone?: string
  is_active?: boolean
  limit?: number
  offset?: number
}

function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    out[k] = v
  }
  return out
}

export async function listShelves(
  params: ListShelvesParams = {},
): Promise<ShelfListResult> {
  const resp = await api.get<ShelfListResult>('/shelves', {
    params: cleanParams(params),
  })
  return resp.data
}

export interface CreateShelfPayload {
  code: string
  name: string
  zone: string
  location?: string
  display_order?: number
}

export async function createShelf(payload: CreateShelfPayload): Promise<Shelf> {
  const resp = await api.post<Shelf>('/shelves', payload)
  return resp.data
}

export interface UpdateShelfPayload {
  name?: string
  location?: string
  is_active?: boolean
  display_order?: number
}

export async function updateShelf(
  id: string,
  payload: UpdateShelfPayload,
): Promise<Shelf> {
  const resp = await api.post<Shelf>(`/shelves/${id}/update`, payload)
  return resp.data
}

export async function deactivateShelf(id: string): Promise<Shelf> {
  const resp = await api.post<Shelf>(`/shelves/${id}/deactivate`)
  return resp.data
}

export async function getShelfProcesses(
  id: string,
): Promise<ShelfWithProcesses> {
  const resp = await api.get<ShelfWithProcesses>(`/shelves/${id}/processes`)
  return resp.data
}

export async function setShelfProcesses(
  id: string,
  payload: SetShelfProcessesPayload,
): Promise<ShelfWithProcesses> {
  const resp = await api.post<ShelfWithProcesses>(
    `/shelves/${id}/processes`,
    payload,
  )
  return resp.data
}

/**
 * 共享 HMI RETURN 卡片网格 picker 数据源。
 * 后端 `GET /shelves/for-return?next_process_id=...`
 * 返回候选架列表（按 current_load ASC 排序）+ 系统推荐架 id。
 *
 * 错误：20506 BIZ_SHELF_NO_MATCH_FOR_PROCESS（没有 active 架映射该 process）
 */
export async function listShelvesForReturn(
  nextProcessId: string,
): Promise<ShelfForReturnResult> {
  const resp = await api.get<ShelfForReturnResult>('/shelves/for-return', {
    params: { next_process_id: nextProcessId },
  })
  return resp.data
}

/**
 * 2026-07-13 新增：共享 HMI INSPECT 卡片网格 picker 数据源。
 * 后端 `GET /shelves/for-inspection`
 * 返回 active INSPECTION 货架列表（按 current_load ASC 排序）+ 推荐架。
 *
 * 错误：20506 BIZ_SHELF_NO_MATCH_FOR_PROCESS（没有 INSPECTION 架或用户
 * scope 内无 INSPECTION 架）。
 */
export async function listShelvesForInspection(): Promise<ShelfForReturnResult> {
  const resp = await api.get<ShelfForReturnResult>('/shelves/for-inspection')
  return resp.data
}

/**
 * 2026-07-17 新增：批量取所有 active 货架的工序 id 列表。
 * 后端 `GET /shelves/processes`
 * 返回 `{items: [{shelf_id, process_ids}, ...]}`——空映射的货架不出现在 items 中。
 *
 * 给 `useShelfProcessFilter` composable 一次性消费，避免弹窗打开时
 * N+1 次 `GET /shelves/{id}/processes` 调用。
 */
export interface ShelfProcessMapping {
  shelf_id: string
  process_ids: string[]
}
export interface ShelfProcessMappingsResult {
  items: ShelfProcessMapping[]
}
export async function getAllShelfProcessMappings(): Promise<ShelfProcessMappingsResult> {
  const resp = await api.get<ShelfProcessMappingsResult>('/shelves/processes')
  return resp.data
}