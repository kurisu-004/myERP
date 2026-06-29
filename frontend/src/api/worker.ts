// 后端工人 API 封装（fetch）。统一处理 R<T> 解包。

import type {
  Worker,
  WorkerCreatePayload,
  WorkerListResult,
  WorkerUpdatePayload,
} from '@/types/worker'

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

export async function listWorkers(params: {
  name_like?: string
  is_active?: boolean
  limit?: number
  offset?: number
} = {}): Promise<WorkerListResult> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    qs.set(k, String(v))
  }
  const url = `/api/v1/workers${qs.toString() ? '?' + qs.toString() : ''}`
  return unwrap<WorkerListResult>(await fetch(url))
}

export async function getWorker(id: number): Promise<Worker> {
  return unwrap<Worker>(await fetch(`/api/v1/workers/${id}`))
}

export async function createWorker(payload: WorkerCreatePayload): Promise<Worker> {
  const resp = await fetch('/api/v1/workers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return unwrap<Worker>(resp)
}

export async function updateWorker(
  id: number,
  payload: WorkerUpdatePayload,
): Promise<Worker> {
  const resp = await fetch(`/api/v1/workers/${id}/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return unwrap<Worker>(resp)
}

export async function deactivateWorker(id: number): Promise<Worker> {
  const resp = await fetch(`/api/v1/workers/${id}/deactivate`, {
    method: 'POST',
  })
  return unwrap<Worker>(resp)
}

export async function reactivateWorker(id: number): Promise<Worker> {
  const resp = await fetch(`/api/v1/workers/${id}/reactivate`, {
    method: 'POST',
  })
  return unwrap<Worker>(resp)
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