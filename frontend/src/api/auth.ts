// 账号登录 / 会话 / 当前账号 API。
//
// 走 @/api/http 的统一 axios 客户端：
// - /auth/login 公开（localStorage 里没 token 时请求拦截器 no-op）
// - /auth/me / /auth/logout 自动挂 Authorization

import { api } from '@/api/http'
import type { CurrentUser } from '@/types/user'

export interface LoginResponse {
  token: string
  user: CurrentUser
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const resp = await api.post<LoginResponse>('/auth/login', { username, password })
  return resp.data
}

export async function me(): Promise<CurrentUser> {
  const resp = await api.get<CurrentUser>('/auth/me')
  return resp.data
}

export async function logout(): Promise<void> {
  // no-op：客户端丢 token 即可。这里容忍失败（不清 localStorage 也不抛）。
  try {
    await api.post('/auth/logout')
  } catch {
    /* noop */
  }
}