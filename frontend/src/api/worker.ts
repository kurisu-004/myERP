// 后端工人 API（走 @/api/http 统一 axios 客户端）。

import { api } from '@/api/http'
import type {
  Worker,
  WorkerCreatePayload,
  WorkerListResult,
  WorkerUpdatePayload,
} from '@/types/worker'

export interface ListWorkersParams {
  name_like?: string
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

export async function listWorkers(
  params: ListWorkersParams = {},
): Promise<WorkerListResult> {
  const resp = await api.get<WorkerListResult>('/workers', {
    params: cleanParams(params),
  })
  return resp.data
}

export async function getWorker(id: string): Promise<Worker> {
  const resp = await api.get<Worker>(`/workers/${id}`)
  return resp.data
}

export async function createWorker(payload: WorkerCreatePayload): Promise<Worker> {
  const resp = await api.post<Worker>('/workers', payload)
  return resp.data
}

export async function updateWorker(
  id: string,
  payload: WorkerUpdatePayload,
): Promise<Worker> {
  const resp = await api.post<Worker>(`/workers/${id}/update`, payload)
  return resp.data
}

export async function deactivateWorker(id: string): Promise<Worker> {
  const resp = await api.post<Worker>(`/workers/${id}/deactivate`)
  return resp.data
}

export async function reactivateWorker(id: string): Promise<Worker> {
  const resp = await api.post<Worker>(`/workers/${id}/reactivate`)
  return resp.data
}

// ============ 工牌扫码查找（客户端缓存） ============
//
// 后端目前没有 GET /workers/by-badge/{code} 端点，且本轮不动后端。
// 折中方案：拉一次在职工人列表到本地缓存，按 badge_code 客户端精匹配。
// 任何对工人增删改之后必须调 invalidateWorkerCache() 让缓存失效。

const CACHE_TTL_MS = 60_000
const CACHE_LIMIT = 500

interface WorkerCache {
  list: Worker[]
  ts: number
}

let workerCache: WorkerCache | null = null

/** 强制失效缓存；下次 findWorkerByBadge 会重新拉 */
export function invalidateWorkerCache(): void {
  workerCache = null
}

/**
 * 按工牌码精确匹配工人。
 * - 命中缓存：本地 Array.find，零网络开销
 * - 缓存 miss/过期：拉 listWorkers 重建
 * - 没找到：返回 null（不抛错，调用方按业务决定提示）
 */
export async function findWorkerByBadge(badgeCode: string): Promise<Worker | null> {
  const code = badgeCode.trim()
  if (!code) return null

  const now = Date.now()
  if (!workerCache || now - workerCache.ts > CACHE_TTL_MS) {
    const res = await listWorkers({ is_active: true, limit: CACHE_LIMIT })
    workerCache = { list: res.items, ts: now }
  }
  return workerCache.list.find((w) => w.badge_code === code) ?? null
}