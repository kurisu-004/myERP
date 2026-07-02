// 后端工人 API（走 @/api/http 统一 axios 客户端）。

import { ApiError, api } from '@/api/http'
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

// ============ 工牌扫码定位 ============
//
// 旧实现：拉一次 GET /workers?is_active=true&limit=500 → 客户端 Array.find。
// 问题：(1) 整张工人表被无权用户拿走（信息泄露 / 越权）；
//       (2) SHELF_ACCOUNT 根本无权调 GET /workers（router 强制 MANAGER），原本就是 403 隐患；
//       (3) 500 条硬上限导致工人 >500 时扫描误报；
//       (4) 60s TTL 导致新增 / 停用延迟生效。
// 新实现：POST /workers/verify-badge 单点 query；权限 = require_auth()，
//       MANAGER 与 SHELF_ACCOUNT 都能调。后端在 service 层做 is_active 校验。

// 后端错误码：20201 = BIZ_WORKER_NOT_FOUND, 20202 = BIZ_WORKER_INACTIVE。
// 这两种是扫描时的"未识别"业务态，前端按 null 处理；其他错误原样抛出。
const WORKER_NOT_FOUND = 20201
const WORKER_INACTIVE = 20202

/**
 * 按工牌码精确匹配工人。
 * - 命中且在职 → 返回 Worker。
 * - 不存在 / 已停用 → 返回 null（不抛错，调用方按业务决定提示文案）。
 * - 网络 / 其他错误 → 原样抛 ApiError。
 */
export async function findWorkerByBadge(badgeCode: string): Promise<Worker | null> {
  const code = badgeCode.trim()
  if (!code) return null

  try {
    const resp = await api.post<Worker>('/workers/verify-badge', {
      badge_code: code,
    })
    return resp.data
  } catch (e) {
    if (e instanceof ApiError && (e.code === WORKER_NOT_FOUND || e.code === WORKER_INACTIVE)) {
      return null
    }
    throw e
  }
}