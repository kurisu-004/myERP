// 统一 HTTP 客户端（axios 封装）。
//
// 四件事：
// 1) baseURL = `/api/v1`，所有 api/*.ts 不再写前缀。
// 2) 请求拦截：自动从 localStorage['auth_session'] 取 token，挂 `Authorization: Bearer <token>`。
// 3) 响应拦截：
//    a) 解 `{code, message, data}` 信封 → 调用方拿到的是原始 data。
//    b) 【2026-07-10 新增】token 自动刷新：
//       - reactive：收到 40102（access 过期）→ 调 /auth/refresh 换新 token → 重试原请求；
//       - proactive：每次成功响应都看一眼 exp，剩余 < 5min 就后台 fire-and-forget 刷新。
//    c) 雪崩防御：模块级 refreshPromise 队列，并发 40102 只触发一次 /auth/refresh。
//    d) code !== 0 → 抛 `ApiError(code, message)`，调用方用 try/catch 即可拿到业务错误码。
//
// 4) 【2026-07-10 新增】refreshClient：无任何拦截器的裸 axios 实例，专门给
//    /auth/refresh 用，避免响应拦截器里的 40102 → refresh 链路递归触发。
//
// 失败兜底：refresh 失败 → dispatchEvent('auth:logout')，由 main.ts 监听后
// router.replace('/login')。session 失效的统一入口。

import axios, {
  AxiosError,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { decodeJwt } from '@/utils/jwt'
import { refreshTokens, type LoginResponse } from '@/api/auth'

const STORAGE_KEY = 'auth_session'

export const api = axios.create({
  baseURL: '/api/v1',
  // 不显式设 Content-Type：axios 会按 body 类型自动选 application/json / multipart/form-data。
  timeout: 30_000,
  // FastAPI 期望数组参数格式: ?statuses=A&statuses=B（无 [] 后缀）
  paramsSerializer: (params) => {
    const parts: string[] = []
    for (const key of Object.keys(params)) {
      const val = params[key]
      if (val === undefined || val === null) continue
      if (Array.isArray(val)) {
        for (const v of val) {
          parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(v)}`)
        }
      } else {
        parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(val)}`)
      }
    }
    return parts.join('&')
  },
})

/**
 * 专用 refresh 客户端：无任何拦截器，仅给 /auth/refresh 用。
 *
 * 为什么独立一份：响应拦截器里"40102 → 调 refreshTokens"如果走 `api` 实例，
 * refreshTokens 失败 → 抛 ApiError → 又进响应拦截器 → 又触发 refresh 逻辑 → 递归。
 * 用裸实例把 /auth/refresh 隔离在拦截器之外。
 */
export const refreshClient = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
  paramsSerializer: (params) => {
    const parts: string[] = []
    for (const key of Object.keys(params)) {
      const val = params[key]
      if (val === undefined || val === null) continue
      if (Array.isArray(val)) {
        for (const v of val) {
          parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(v)}`)
        }
      } else {
        parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(val)}`)
      }
    }
    return parts.join('&')
  },
})

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

function isEnvelope(v: unknown): v is ApiEnvelope<unknown> {
  return (
    !!v &&
    typeof v === 'object' &&
    typeof (v as ApiEnvelope<unknown>).code === 'number' &&
    'message' in (v as ApiEnvelope<unknown>) &&
    'data' in (v as ApiEnvelope<unknown>)
  )
}

// ===== storage helpers =====
function readToken(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as { token?: string }
    return s?.token ?? null
  } catch {
    return null
  }
}

function readRefreshToken(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as { refresh_token?: string | null }
    return s?.refresh_token ?? null
  } catch {
    return null
  }
}

/**
 * 把新一对 token 写回 localStorage，并通知 useAuthSession 更新 module-level refs。
 * 不直接 import useAuthSession（会引入循环依赖），走 CustomEvent 解耦。
 */
function persistTokens(pair: LoginResponse): void {
  let cur: { token: string; refresh_token: string | null; user: unknown } = {
    token: '',
    refresh_token: null,
    user: null,
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) cur = { ...cur, ...JSON.parse(raw) }
  } catch {
    /* 损坏的 storage 当空对象处理 */
  }
  cur.token = pair.token
  cur.refresh_token = pair.refresh_token
  cur.user = pair.user
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cur))
  window.dispatchEvent(new CustomEvent('auth:tokens-refreshed', { detail: pair }))
}

// ===== token 自动刷新状态 =====
let refreshPromise: Promise<LoginResponse> | null = null
let cachedAccessExp: number | null = null
let lastProactiveFireMs = 0

/** 剩余寿命 < 该秒数就触发 proactive 刷新。 */
const REFRESH_AHEAD_SECONDS = 5 * 60
/** proactive 节流：30s 内最多触发一次（避免短时间内连续刷新）。 */
const PROACTIVE_THROTTLE_MS = 30_000

/**
 * 实际执行 refresh：读 localStorage 里的 refresh_token，调 /auth/refresh，
 * 把新一对 token 写回 storage（persistTokens）。
 *
 * 失败抛 ApiError；调用方（拦截器）负责 dispatch auth:logout。
 */
async function doRefresh(): Promise<LoginResponse> {
  const rt = readRefreshToken()
  if (!rt) {
    throw new ApiError(40103, 'no refresh token')
  }
  const fresh = await refreshTokens(rt)
  persistTokens(fresh)
  cachedAccessExp = decodeJwt(fresh.token)?.exp ?? null
  return fresh
}

/**
 * 雪崩队列：第一个 40102 触发 doRefresh，后续 40102 复用同一个 promise；
 * 完成后清空（setTimeout 让微任务队列里的消费者先看到结果）。
 */
function getOrCreateRefresh(): Promise<LoginResponse> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        return await doRefresh()
      } finally {
        setTimeout(() => {
          refreshPromise = null
        }, 0)
      }
    })()
  }
  return refreshPromise
}

/** Proactive：每次成功响应后检查 exp，剩余 < 5min 就 fire-and-forget 刷新。 */
function maybeProactiveRefresh(): void {
  if (cachedAccessExp === null) {
    const t = readToken()
    if (!t) return
    cachedAccessExp = decodeJwt(t)?.exp ?? null
    if (cachedAccessExp === null) return
  }
  const nowSec = Math.floor(Date.now() / 1000)
  if (cachedAccessExp - nowSec > REFRESH_AHEAD_SECONDS) return
  if (Date.now() - lastProactiveFireMs < PROACTIVE_THROTTLE_MS) return
  lastProactiveFireMs = Date.now()
  void getOrCreateRefresh().catch(() => {
    /* reactive 路径会兜底；这里只 fire-and-forget */
  })
}

// ===== 请求拦截：挂 Authorization =====
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = readToken()
  if (token) {
    // 用 .set 避免某些 axios 版本对 headers 直接赋值的 readonly 警告。
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

// ===== 响应拦截：解信封 + 自动刷新 =====
api.interceptors.response.use(
  (response: AxiosResponse) => {
    const payload = response.data
    if (isEnvelope(payload)) {
      if (payload.code !== 0) {
        throw new ApiError(payload.code, payload.message, response)
      }
      // 直接把 response.data 替换成解封后的 data，保持 `api.get<T>()` 的 .data 语义。
      response.data = payload.data
    }
    // 非标准响应（如文件 blob / 文本）原样返回
    maybeProactiveRefresh()
    return response
  },
  async (error: AxiosError) => {
    // blob 响应的 error body 也是 Blob；isEnvelope(blob) 返回 false 会让
    // 401 自动刷新失效。先把 Blob body 读成文本再尝试 JSON parse。
    let payload: unknown = error.response?.data
    if (payload instanceof Blob) {
      try {
        const text = await payload.text()
        payload = text ? JSON.parse(text) : null
      } catch {
        payload = null
      }
    }
    if (!isEnvelope(payload)) {
      throw new ApiError(
        error.response?.status ?? 0,
        error.message || 'network error',
        error.response,
      )
    }

    const apiErr = new ApiError(payload.code, payload.message, error.response)
    const cfg = error.config as (InternalAxiosRequestConfig & { _isRetryAfterRefresh?: boolean }) | undefined

    // 仅在 access 过期且非 refresh 重试时触发自动刷新。
    const shouldRefresh =
      apiErr.code === 40102 && cfg && !cfg._isRetryAfterRefresh

    if (!shouldRefresh) throw apiErr

    try {
      const fresh = await getOrCreateRefresh()
      // 用新 token 重试原请求；标记 _isRetryAfterRefresh 防递归
      const retryCfg = {
        ...cfg,
        headers: { ...(cfg.headers ?? {}), Authorization: `Bearer ${fresh.token}` },
        _isRetryAfterRefresh: true,
      } as InternalAxiosRequestConfig & { _isRetryAfterRefresh?: boolean }
      return await api.request(retryCfg)
    } catch (refreshErr) {
      // refresh 失败：触发全局登出事件，main.ts 监听后 router.replace('/login')
      window.dispatchEvent(new CustomEvent('auth:logout'))
      throw refreshErr
    }
  },
)

/** 业务异常：code !== 0 时抛出；调用方用 try/catch + (e as ApiError).code 取错误码。 */
export class ApiError extends Error {
  public readonly code: number
  public readonly response: unknown

  constructor(code: number, message: string, response?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.response = response
  }

  /** 是否为"未登录 / token 失效"——由调用方决定如何处理（路由跳转 / 重新登录）。 */
  get isAuthError(): boolean {
    return this.code === 40101 || this.code === 40102 || this.code === 40103
  }
}
