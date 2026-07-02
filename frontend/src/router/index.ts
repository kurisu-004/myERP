import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { MenuNode } from '@/types/menu'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    icon?: string
    breadcrumb?: Array<{ label: string; path?: string }>
    requireAuth?: boolean
    /** 该路由所需的菜单 code；缺省表示不依赖菜单（公开 / 已登录即可）。
     *  守卫会校验"用户的菜单树中是否包含该 code"，单一权限源。 */
    menuCode?: string
  }
}

const routes: RouteRecordRaw[] = [
  // 通用登录页（脱离 MainLayout，独立全屏）
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/auth/Login.vue'),
    meta: { title: '登录' },
  },
  // MainLayout 子树
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    meta: { requireAuth: true },
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '首页', icon: 'House', breadcrumb: [{ label: '首页' }], menuCode: 'home' },
      },
      {
        path: 'parts',
        name: 'PartsList',
        component: () => import('@/views/parts/PartsList.vue'),
        meta: { title: '零件一览', icon: 'Box', menuCode: 'parts_list', breadcrumb: [{ label: '订单管理', path: '/parts' }, { label: '零件一览' }] },
      },
      {
        path: 'parts/new',
        name: 'PartsNew',
        component: () => import('@/views/parts/PartBatchNew.vue'),
        meta: { title: '新建零件', menuCode: 'parts_new', breadcrumb: [{ label: '订单管理', path: '/parts' }, { label: '新建零件' }] },
      },
      {
        path: 'parts/:id(\\d+)',
        name: 'PartsDetail',
        component: () => import('@/views/parts/PartDetail.vue'),
        meta: { title: '零件详情', menuCode: 'parts_list', breadcrumb: [{ label: '订单管理', path: '/parts' }, { label: '零件一览', path: '/parts' }, { label: '详情' }] },
        props: true,
      },
      {
        path: 'assemblies',
        name: 'AssemblyList',
        component: () => import('@/views/assemblies/AssemblyList.vue'),
        meta: { title: '装配件一览', icon: 'Connection', menuCode: 'assemblies_list', breadcrumb: [{ label: '订单管理', path: '/assemblies' }, { label: '装配件一览' }] },
      },
      {
        path: 'assemblies/new',
        name: 'AssemblyCreate',
        component: () => import('@/views/assemblies/AssemblyCreate.vue'),
        meta: { title: '新建装配件', menuCode: 'assemblies_new', breadcrumb: [{ label: '订单管理', path: '/assemblies' }, { label: '装配件一览', path: '/assemblies' }, { label: '新建' }] },
      },
      {
        path: 'assemblies/:id(\\d+)',
        name: 'AssemblyDetail',
        component: () => import('@/views/assemblies/AssemblyDetail.vue'),
        meta: { title: '装配件详情', menuCode: 'assemblies_list', breadcrumb: [{ label: '订单管理', path: '/assemblies' }, { label: '装配件一览', path: '/assemblies' }, { label: '详情' }] },
        props: true,
      },
      {
        path: 'workers',
        name: 'WorkerList',
        component: () => import('@/views/WorkerList.vue'),
        meta: { title: '工人一览', icon: 'User', menuCode: 'workers_list', breadcrumb: [{ label: '权限管理', path: '/workers' }, { label: '工人一览' }] },
      },
      {
        path: 'users',
        name: 'UserList',
        component: () => import('@/views/users/UserList.vue'),
        meta: { title: '账号管理', icon: 'Key', menuCode: 'users_list', breadcrumb: [{ label: '权限管理', path: '/users' }, { label: '账号管理' }] },
      },
      {
        path: 'shelves',
        name: 'ShelfList',
        component: () => import('@/views/shelves/ShelfList.vue'),
        meta: { title: '货架管理', icon: 'Platform', menuCode: 'shelves_list', breadcrumb: [{ label: '车间', path: '/shelves' }, { label: '货架管理' }] },
      },
    ],
  },
  // 工位扫码台
  {
    path: '/scan',
    meta: { requireAuth: true, menuCode: 'scan_badge' },
    children: [
      { path: '', redirect: '/scan/badge' },
      { path: 'badge', name: 'ScanBadge', component: () => import('@/views/scan/ScanBadgeGate.vue'), meta: { title: '扫码台 · 工牌识别', menuCode: 'scan_badge' } },
      { path: 'action', name: 'ScanAction', component: () => import('@/views/scan/ScanActionPicker.vue'), meta: { title: '扫码台 · 操作选择', menuCode: 'scan_badge' } },
      { path: 'parts', name: 'ScanParts', component: () => import('@/views/scan/ScanPartsWork.vue'), meta: { title: '扫码台 · 扫码报工', menuCode: 'scan_badge' } },
    ],
  },
]

const router = createRouter({ history: createWebHistory(), routes })

/** DFS 在用户的菜单树中查找指定 code。 */
function treeContainsCode(tree: MenuNode[], code: string): boolean {
  const stack: MenuNode[] = [...tree]
  while (stack.length > 0) {
    const n = stack.pop()!
    if (n.code === code) return true
    if (n.children.length > 0) stack.push(...n.children)
  }
  return false
}

// 全局前置守卫
router.beforeEach(async (to, _from, next) => {
  const { useAuthSession } = await import('@/composables/useAuthSession')
  const { isAuthenticated, refreshOrLogout, menus, hasRole } = useAuthSession()

  // 1) 未登录 → /login
  if (to.meta.requireAuth || to.matched.some((r) => r.meta.requireAuth)) {
    if (!isAuthenticated()) {
      const ok = await refreshOrLogout(router)
      if (!ok) return
    }
  }

  // 2) menuCode 校验：菜单树中存在对应 code 即放行。
  //    单一权限源 —— 不再有独立的 allowRoles 白名单。
  //    登录跳转降级目标按角色定：MANAGER 落 /dashboard，其他角色落 /scan/badge。
  const code = to.meta.menuCode
  if (code && !treeContainsCode(menus(), code)) {
    return next(hasRole('MANAGER') ? '/dashboard' : '/scan/badge')
  }

  next()
})

export default router