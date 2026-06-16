import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T | null
}

const TOKEN_KEY = 'myerp.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token === null) {
    localStorage.removeItem(TOKEN_KEY)
  } else {
    localStorage.setItem(TOKEN_KEY, token)
  }
}

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

http.interceptors.request.use((cfg: InternalAxiosRequestConfig) => {
  const token = getToken()
  if (token) {
    cfg.headers.set('Authorization', `Bearer ${token}`)
  }
  return cfg
})

export class ApiError extends Error {
  code: number
  httpStatus: number
  data: unknown

  constructor(message: string, code: number, httpStatus: number, data: unknown) {
    super(message)
    this.code = code
    this.httpStatus = httpStatus
    this.data = data
  }
}

http.interceptors.response.use(
  (resp: AxiosResponse<ApiEnvelope<unknown>>) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        throw new ApiError(body.message ?? 'api error', body.code, resp.status, body.data)
      }
      // 把 data 解包返回
      return { ...resp, data: body.data } as unknown as AxiosResponse
    }
    return resp
  },
  (err: AxiosError<ApiEnvelope<unknown>>) => {
    const status = err.response?.status ?? 0
    if (status === 401) {
      setToken(null)
      if (location.pathname !== '/login') {
        location.href = `/login?redirect=${encodeURIComponent(location.pathname)}`
      }
    }
    const body = err.response?.data
    if (body && typeof body === 'object' && 'code' in body) {
      throw new ApiError(body.message ?? 'api error', body.code, status, body.data)
    }
    throw new ApiError(err.message || 'network error', -1, status, null)
  },
)

export default http
