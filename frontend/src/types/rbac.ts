export interface User {
  id: string
  employee_no: string
  username: string
  name: string
  is_active: number
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export interface Role {
  id: string
  code: string
  name: string
  description: string | null
  is_builtin: number
  created_at: string
  updated_at: string
}

export interface Permission {
  id: string
  code: string
  name: string
  type: 'MENU' | 'API' | 'BUTTON' | 'DATA' | string
  parent_id: string | null
  path: string | null
  icon: string | null
  component: string | null
  sort_order: number
  is_enabled: number
  created_at: string
  updated_at: string
}

export interface MenuNode {
  id: string
  code: string
  name: string
  path: string | null
  icon: string | null
  component: string | null
  sort_order: number
  children: MenuNode[]
}

export interface MeResponse {
  user: User
  roles: Role[]
  permissions: string[]
  menus: MenuNode[]
  is_superadmin: boolean
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface PageResponse<T> {
  items: T[]
  total: number
  page: number
  size: number
  pages: number
}
