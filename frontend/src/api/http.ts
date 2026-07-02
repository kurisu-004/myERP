// 统一 HTTP 客户端（axios 封装）。
//
// 三件事：
// 1) baseURL = `/api/v1`，所有 api/*.ts 不再写前缀。
// 2) 请求拦截：自动从 localStorage['auth_session'] 取 token，挂 `Authorization: Bearer <token>`。
// 3) 响应拦截：解 `{code, message, data}` 信封。
//    - code === 0 → 解出 `data`，调用方拿到的是原始数据。
//    - code !== 0 → 抛 `ApiError(code, message)`，调用方用 try/catch 即可拿到业务错误码。
//
// 不自动登出：401 由 router guard 的 `refreshOrLogout` 处理（每次导航时拉一次
// /auth/me 验 token），403 由业务层自行决定。拦截器只负责"格式化错误"，不
// 触碰 session。

import axios, {
  AxiosError,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'

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

// ===== 请求拦截：挂 Authorization =====
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = readToken()
  if (token) {
    // 用 .set 避免某些 axios 版本对 headers 直接赋值的 readonly 警告。
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

// ===== 响应拦截：解信封 =====
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
    return response
  },
  (error: AxiosError) => {
    const payload = error.response?.data
    if (isEnvelope(payload)) {
      throw new ApiError(payload.code, payload.message, error.response)
    }
    throw new ApiError(
      error.response?.status ?? 0,
      error.message || 'network error',
      error.response,
    )
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
    return this.code === 40101 || this.code === 40102
  }
}