// composables/useAuthSession.ts
//
// 通用账号 session（不是业务 worker；后者保持在 useScanSession）。
// - 模块级单例，跨组件共享（与 useScanSession / useBarcodeScanner 同构）。
// - localStorage key: 'auth_session'；内容 { token, user }。
// - login() → POST /auth/login；logout() → 清 storage + 回 /login。
// - hasRole / canOperateShelf 供路由守卫和组件使用。
// - menus() 返回当前用户的菜单树（来自后端 CurrentUser.menus），供
//   MainLayout 渲染侧边栏 + 路由守卫校验 menuCode。

import { ref, type Ref } from 'vue'
import { login as apiLogin, logout as apiLogout, me as apiMe } from '@/api/auth'
import type { CurrentUser } from '@/types/user'
import type { MenuNode } from '@/types/menu'

interface StoredSession { token: string; user: CurrentUser }

const user = ref<CurrentUser | null>(null) as Ref<CurrentUser | null>
const token = ref<string | null>(null)

function loadFromStorage(): boolean {
  try {
    const raw = localStorage.getItem('auth_session')
    if (!raw) return false
    const s: StoredSession = JSON.parse(raw)
    if (!s.token || !s.user) return false
    // 兼容旧版本 localStorage（没有 menus 字段）：补默认值，下次 /auth/me 会刷新。
    s.user.menus = s.user.menus ?? []
    token.value = s.token
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
  localStorage.setItem('auth_session', JSON.stringify({ token: token.value, user: user.value }))
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
    user.value = resp.user
    saveToStorage()
    return resp.user
  }

  async function logout(): Promise<void> {
    await apiLogout()
    token.value = null
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
    menus,
    hasMenuCode,
    getAuthHeader,
    login,
    logout,
    refreshOrLogout,
  }
}
