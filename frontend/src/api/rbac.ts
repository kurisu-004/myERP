import http from './http'
import type {
  MeResponse,
  TokenResponse,
  PageResponse,
  User,
  Role,
  Permission,
  MenuNode,
} from '@/types/rbac'

// ----- Auth -----
export function login(username: string, password: string) {
  return http.post<unknown, { data: TokenResponse }>('/auth/login', {
    username,
    password,
  })
}

export function fetchMe() {
  return http.get<unknown, { data: MeResponse }>('/auth/me')
}

// ----- User -----
export interface UserListQuery {
  page: number
  size: number
  keyword?: string
}

export function listUsers(q: UserListQuery) {
  return http.get<unknown, { data: PageResponse<User> }>('/rbac/users', {
    params: q,
  })
}

export interface UserCreatePayload {
  employee_no: string
  username: string
  name: string
  password: string
  role_ids: number[]
}

export function createUser(payload: UserCreatePayload) {
  return http.post<unknown, { data: User }>('/rbac/users', payload)
}

export interface UserUpdatePayload {
  name?: string
  password?: string
  is_active?: number
  role_ids?: number[]
}

export function updateUser(userId: string, payload: UserUpdatePayload) {
  return http.put<unknown, { data: User }>(`/rbac/users/${userId}`, payload)
}

export function deleteUser(userId: string) {
  return http.delete<unknown, { data: { message: string } }>(`/rbac/users/${userId}`)
}

export function getUserDetail(userId: string) {
  return http.get<unknown, { data: User & { roles: Role[] } }>(`/rbac/users/${userId}`)
}

export function assignUserRoles(userId: string, roleIds: number[]) {
  return http.put<unknown, { data: User & { roles: Role[] } }>(
    `/rbac/users/${userId}/roles`,
    { role_ids: roleIds },
  )
}

// ----- Role -----
export function listRoles(q: UserListQuery) {
  return http.get<unknown, { data: PageResponse<Role> }>('/rbac/roles', {
    params: q,
  })
}

export function listAllRoles() {
  return http.get<unknown, { data: Role[] }>('/rbac/roles/all')
}

export interface RoleCreatePayload {
  code: string
  name: string
  description?: string | null
  permission_ids: number[]
}

export function createRole(payload: RoleCreatePayload) {
  return http.post<unknown, { data: Role }>('/rbac/roles', payload)
}

export interface RoleUpdatePayload {
  name?: string
  description?: string | null
  permission_ids?: number[]
}

export function updateRole(roleId: string, payload: RoleUpdatePayload) {
  return http.put<unknown, { data: Role }>(`/rbac/roles/${roleId}`, payload)
}

export function deleteRole(roleId: string) {
  return http.delete<unknown, { data: { message: string } }>(`/rbac/roles/${roleId}`)
}

// ----- Permission -----
export function listPermissions() {
  return http.get<unknown, { data: Permission[] }>('/rbac/permissions')
}

export interface PermissionCreatePayload {
  code: string
  name: string
  type: string
  parent_id?: number | null
  path?: string | null
  icon?: string | null
  component?: string | null
  sort_order?: number
  is_enabled?: number
}

export function createPermission(payload: PermissionCreatePayload) {
  return http.post<unknown, { data: Permission }>('/rbac/permissions', payload)
}

export interface PermissionUpdatePayload {
  name?: string
  parent_id?: number | null
  path?: string | null
  icon?: string | null
  component?: string | null
  sort_order?: number
  is_enabled?: number
}

export function updatePermission(permId: string, payload: PermissionUpdatePayload) {
  return http.put<unknown, { data: Permission }>(`/rbac/permissions/${permId}`, payload)
}

export type { MeResponse, TokenResponse, PageResponse, User, Role, Permission, MenuNode }
