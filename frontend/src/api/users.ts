// 账号管理 API（走 @/api/http 统一 axios 客户端）。

import { api } from '@/api/http'
import type { UserOut, UserRoleOut, UserListResult } from '@/types/user'

export interface ListUsersParams {
  username_like?: string
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

export async function listUsers(
  params: ListUsersParams = {},
): Promise<UserListResult> {
  const resp = await api.get<UserListResult>('/users', { params: cleanParams(params) })
  return resp.data
}

export interface CreateUserPayload {
  username: string
  password: string
  full_name: string
  phone?: string
}

export async function createUser(payload: CreateUserPayload): Promise<UserOut> {
  const resp = await api.post<UserOut>('/users', payload)
  return resp.data
}

export interface UpdateUserPayload {
  full_name?: string
  phone?: string
  password?: string
  is_active?: boolean
}

export async function updateUser(
  id: string,
  payload: UpdateUserPayload,
): Promise<UserOut> {
  const resp = await api.post<UserOut>(`/users/${id}/update`, payload)
  return resp.data
}

export async function deactivateUser(id: string): Promise<UserOut> {
  const resp = await api.post<UserOut>(`/users/${id}/deactivate`)
  return resp.data
}

/** 管理员重置指定账号密码为默认口令 changeme（后端会轮转其 refresh token）。 */
export async function resetUserPassword(id: string): Promise<UserOut> {
  const resp = await api.post<UserOut>(`/users/${id}/reset-password`)
  return resp.data
}

export async function listUserRoles(userId: string): Promise<UserRoleOut[]> {
  const resp = await api.get<UserRoleOut[]>(`/users/${userId}/roles`)
  return resp.data
}

export interface AddUserRolePayload {
  role: string
  scope_type?: string | null
  scope_id?: string | null
}

export async function addUserRole(
  userId: string,
  payload: AddUserRolePayload,
): Promise<UserRoleOut> {
  const resp = await api.post<UserRoleOut>(`/users/${userId}/roles`, payload)
  return resp.data
}

export async function removeUserRole(userId: string, roleId: string): Promise<void> {
  await api.post(`/users/${userId}/roles/${roleId}/remove`)
}