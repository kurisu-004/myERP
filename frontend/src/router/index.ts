import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '首页', icon: 'House', breadcrumb: ['首页'] },
      },
      {
        path: 'parts',
        name: 'PartsList',
        component: () => import('@/views/parts/PartsList.vue'),
        meta: { title: '零件一览', icon: 'Box', breadcrumb: ['基础数据', '零件一览'] },
      },
      {
        path: 'rbac/users',
        name: 'UserList',
        component: () => import('@/views/rbac/UserList.vue'),
        meta: { title: '用户管理', icon: 'User', breadcrumb: ['系统管理', '用户管理'] },
      },
      {
        path: 'rbac/roles',
        name: 'RoleList',
        component: () => import('@/views/rbac/RoleList.vue'),
        meta: { title: '角色管理', icon: 'UserFilled', breadcrumb: ['系统管理', '角色管理'] },
      },
      {
        path: 'rbac/permissions',
        name: 'PermissionList',
        component: () => import('@/views/rbac/PermissionList.vue'),
        meta: { title: '菜单权限', icon: 'Key', breadcrumb: ['系统管理', '菜单权限'] },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/NotFound.vue'),
    meta: { title: '404', public: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta?.public) return true
  const { useAuthStore } = await import('@/store/auth')
  const auth = useAuthStore()
  // 如果有 token 但还没拉过 me, 先异步拉一次
  if (auth.token && !auth.me) {
    try {
      await auth.loadMe()
    } catch {
      auth.logout()
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  if (!auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
