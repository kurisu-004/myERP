// 货架 API（走 @/api/http 统一 axios 客户端）。

import { api } from '@/api/http'
import type {
  Shelf,
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
}

export async function createShelf(payload: CreateShelfPayload): Promise<Shelf> {
  const resp = await api.post<Shelf>('/shelves', payload)
  return resp.data
}

export interface UpdateShelfPayload {
  name?: string
  location?: string
  is_active?: boolean
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