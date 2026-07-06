// 工序 (Process) API 封装。

import { api } from '@/api/http'
import type {
  Process,
  ProcessCategory,
  ProcessCreatePayload,
  ProcessListResult,
  ProcessUpdatePayload,
} from '@/types/process'

function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

export async function listProcesses(
  params: {
    code_like?: string
    category?: ProcessCategory
    limit?: number
    offset?: number
  } = {},
): Promise<ProcessListResult> {
  const resp = await api.get<ProcessListResult>('/processes', {
    params: cleanParams(params),
  })
  return resp.data
}

export async function getProcess(id: string): Promise<Process> {
  const resp = await api.get<Process>(`/processes/${id}`)
  return resp.data
}

export async function createProcess(
  payload: ProcessCreatePayload,
): Promise<Process> {
  const resp = await api.post<Process>('/processes', payload)
  return resp.data
}

export async function updateProcess(
  id: string,
  payload: ProcessUpdatePayload,
): Promise<Process> {
  const resp = await api.post<Process>(`/processes/${id}/update`, payload)
  return resp.data
}

export async function softDeleteProcess(id: string): Promise<void> {
  await api.post(`/processes/${id}/soft-delete`)
}