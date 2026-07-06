// 工种 (WorkType) API 封装。

import { api } from '@/api/http'
import type {
  SetWorkTypeProcessesPayload,
  WorkType,
  WorkTypeCreatePayload,
  WorkTypeListResult,
  WorkTypeUpdatePayload,
  WorkTypeWithProcesses,
} from '@/types/workType'

function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

export async function listWorkTypes(
  params: { code_like?: string; limit?: number; offset?: number } = {},
): Promise<WorkTypeListResult> {
  const resp = await api.get<WorkTypeListResult>('/work-types', {
    params: cleanParams(params),
  })
  return resp.data
}

export async function getWorkType(id: string): Promise<WorkType> {
  const resp = await api.get<WorkType>(`/work-types/${id}`)
  return resp.data
}

export async function createWorkType(
  payload: WorkTypeCreatePayload,
): Promise<WorkType> {
  const resp = await api.post<WorkType>('/work-types', payload)
  return resp.data
}

export async function updateWorkType(
  id: string,
  payload: WorkTypeUpdatePayload,
): Promise<WorkType> {
  const resp = await api.post<WorkType>(`/work-types/${id}/update`, payload)
  return resp.data
}

export async function softDeleteWorkType(id: string): Promise<void> {
  await api.post(`/work-types/${id}/soft-delete`)
}

export async function getWorkTypeProcesses(
  workTypeId: string,
): Promise<WorkTypeWithProcesses> {
  const resp = await api.get<WorkTypeWithProcesses>(
    `/work-types/${workTypeId}/processes`,
  )
  return resp.data
}

export async function setWorkTypeProcesses(
  workTypeId: string,
  payload: SetWorkTypeProcessesPayload,
): Promise<WorkTypeWithProcesses> {
  const resp = await api.post<WorkTypeWithProcesses>(
    `/work-types/${workTypeId}/processes`,
    payload,
  )
  return resp.data
}