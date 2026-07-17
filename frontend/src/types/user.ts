/** 与后端 enum UserRole 对齐 */
export type UserRole = 'MANAGER' | 'SHELF_ACCOUNT' | 'CLERK' | 'INSPECTOR' | 'CNC_PROGRAMMER'

export interface UserOut {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  username: string
  full_name: string
  phone: string | null
  is_active: boolean
  last_login_at: string | null
  created_at: string
  updated_at: string
  roles: UserRoleOut[]
}

export interface UserRoleOut {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  role: string
  scope_type: string | null
  scope_id: string | null
  shelf_code: string | null
  shelf_name: string | null
}

export interface CurrentUser {
  id: string
  username: string
  full_name: string
  is_active: boolean
  roles: string[]           // 字符串数组，如 ["MANAGER"]
  shelf_ids: string[]       // SHELF_ACCOUNT 时非空
  menus: import('./menu').MenuNode[]  // 登录时拉回，渲染侧边栏 + 路由守卫用
}

export interface UserListResult {
  items: UserOut[]
  total: number
  limit: number
  offset: number
}
