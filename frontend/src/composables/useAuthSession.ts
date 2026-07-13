// composables/useAuthSession.ts
//
// 通用账号 session（不是业务 worker；后者保持在 useScanSession）。
// - 模块级单例，跨组件共享（与 useScanSession / useBarcodeScanner 同构）。
// - localStorage key: 'auth_session'；内容 { token, refresh_token, user }。
// - login() → POST /auth/login（返回双 token）；logout() → 清 storage + 回 /login。
// - hasRole / canOperateShelf 供路由守卫和组件使用。
// - menus() 返回当前用户的菜单树（来自后端 CurrentUser.menus），供
//   MainLayout 渲染侧边栏 + 路由守卫校验 menuCode。
//
// 2026-07-10 新增：
// - storage 多带一个 refresh_token 字段（兼容老条目：缺省当 null）；
// - 监听 'auth:tokens-refreshed' 事件，拦截器刷新成功后同步 module-level refs；
// - login() 把 resp.refresh_token 也存进去；
// - logout() 不变（直接 removeItem 把三件套一起清）。

import { ref, type Ref } from 'vue'
import { login as apiLogin, logout as apiLogout, me as apiMe } from '@/api/auth'
import type { CurrentUser } from '@/types/user'
import type { MenuNode } from '@/types/menu'

interface StoredSession {
  token: string
  /** 2026-07-10 新增：refresh token（7d TTL）。老条目可能缺省，按 null 处理。 */
  refresh_token?: string | null
  user: CurrentUser
}

const user = ref<CurrentUser | null>(null) as Ref<CurrentUser | null>
const token = ref<string | null>(null)
// refresh_token 不暴露给组件（只由 axios 拦截器读），但用模块级常量便于内部测试
let refreshTokenValue: string | null = null

function loadFromStorage(): boolean {
  try {
    const raw = localStorage.getItem('auth_session')
    if (!raw) return false
    const s: StoredSession = JSON.parse(raw)
    if (!s.token || !s.user) return false
    // 兼容旧版本 localStorage（没有 menus 字段）：补默认值，下次 /auth/me 会刷新。
    s.user.menus = s.user.menus ?? []
    token.value = s.token
    refreshTokenValue = s.refresh_token ?? null
    user.value = s.user
    return true
  } catch {
    return false
  }
}

function saveToStorage(): void {
  if (!token.value || !user.value) {
    localStorage.removeItem('auth_session')
    return
  }
  localStorage.setItem(
    'auth_session',
    JSON.stringify({
      token: token.value,
      refresh_token: refreshTokenValue,
      user: user.value,
    }),
  )
}

// 监听拦截器刷新成功的广播事件，同步 module-level refs
if (typeof window !== 'undefined') {
  window.addEventListener('auth:tokens-refreshed', ((e: Event) => {
    const ce = e as CustomEvent<{ token: string; refresh_token: string; user: CurrentUser }>
    const pair = ce.detail
    if (pair?.token) {
      token.value = pair.token
      refreshTokenValue = pair.refresh_token ?? null
      user.value = pair.user
    }
  }) as EventListener)
}

// 启动时尝试恢复
loadFromStorage()

export function useAuthSession() {
  const isAuthenticated = (): boolean => !!token.value && !!user.value

  function hasRole(role: string): boolean {
    return user.value?.roles.includes(role) ?? false
  }

  function canOperateShelf(shelfId: string): boolean {
    if (hasRole('MANAGER')) return true
    if (!hasRole('SHELF_ACCOUNT')) return false
    // 2026-07-13：与后端 CurrentUser.can_operate_shelf 对齐，补 wildcard 兜底
    // （SHELF_ACCOUNT 且未绑任何 active 架 → 视为共享 HMI 通行）。
    if (isWildcardShelfAccount()) return true
    return (user.value?.shelf_ids ?? []).includes(shelfId)
  }

  /**
   * 当前 SHELF_ACCOUNT 账号 scope 到的第一个货架 id（字符串）。
   *
   * 注意：返回 string 而非 number —— 雪花 ID 长度 > 2^53，`Number(...)` 会丢精度
   * （实测 Number("198362487928651776") → 198362487928651780，差 4）。
   * 后端 Pydantic v2 默认 lax 模式会从 JSON string 自动 coerce 到 int。
   */
  function activeShelfId(): string | null {
    const ids = user.value?.shelf_ids ?? []
    return ids.length > 0 ? ids[0] : null
  }

  /**
   * 2026-07-13 新增：当前账号 scope 到的所有货架 id（字符串列表）。
   * SHELF_ACCOUNT 多货架场景用；与后端 user.shelf_ids 一一对应。
   */
  function boundShelves(): string[] {
    return user.value?.shelf_ids ?? []
  }

  /**
   * 2026-07-13 新增：当前账号是否是「共享 HMI 通配」SHELF_ACCOUNT。
   *
   * 判定方式：当前 user SHELF_ACCOUNT 角色 + 未绑任何 active 架
   * （shelf_ids 为空）。后端在 JWT 里也带 `shelf_wildcard`，但 API 响应
   * 没透出；用这个启发式等价于「可对任意 PRODUCTION/INSPECTION 架放行」。
   *
   * 注：边界场景 —— 绑了架但全部被停用 → shelf_ids 也为空，按通配处理。
   * 退化为「按钮可见但提交时被后端 403」，可接受。
   */
  function isWildcardShelfAccount(): boolean {
    return hasRole('SHELF_ACCOUNT') && boundShelves().length === 0
  }

  /** 当前可见菜单树（顶层列表；children 在节点里）。 */
  function menus(): MenuNode[] {
    return user.value?.menus ?? []
  }

  /** DFS 在菜单树中查找指定 code。供路由守卫使用。 */
  function hasMenuCode(code: string): boolean {
    const tree = menus()
    const stack: MenuNode[] = [...tree]
    while (stack.length > 0) {
      const n = stack.pop()!
      if (n.code === code) return true
      if (n.children.length > 0) stack.push(...n.children)
    }
    return false
  }

  function getAuthHeader(): Record<string, string> {
    if (!token.value) return {}
    return { Authorization: `Bearer ${token.value}` }
  }

  async function login(username: string, password: string): Promise<CurrentUser> {
    const resp = await apiLogin(username, password)
    token.value = resp.token
    refreshTokenValue = resp.refresh_token ?? null
    user.value = resp.user
    saveToStorage()
    return resp.user
  }

  async function logout(): Promise<void> {
    await apiLogout()
    token.value = null
    refreshTokenValue = null
    user.value = null
    localStorage.removeItem('auth_session')
  }

  /** 异步守卫：拉 /auth/me 验证 token 仍有效；失败则清 session 跳 /login */
  async function refreshOrLogout(router: { replace: (p: string) => void }): Promise<boolean> {
    try {
      const u = await apiMe()
      // 兼容老后端（没有 menus 字段）
      u.menus = u.menus ?? []
      user.value = u
      return true
    } catch {
      token.value = null
      refreshTokenValue = null
      user.value = null
      localStorage.removeItem('auth_session')
      router.replace('/login')
      return false
    }
  }

  return {
    user,
    token,
    isAuthenticated,
    hasRole,
    canOperateShelf,
    activeShelfId,
    boundShelves,
    isWildcardShelfAccount,
    menus,
    hasMenuCode,
    getAuthHeader,
    login,
    logout,
    refreshOrLogout,
  }
}
