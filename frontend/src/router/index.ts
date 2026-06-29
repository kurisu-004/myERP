import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

// Route meta 类型扩展：breadcrumb 项带 path 才可点击；不写 path 视为当前页（不可点）。
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    icon?: string
    breadcrumb?: Array<{ label: string; path?: string }>
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'parts',
        name: 'PartsList',
        component: () => import('@/views/parts/PartsList.vue'),
        meta: {
          title: '零件一览',
          icon: 'Box',
          breadcrumb: [
            { label: '订单管理', path: '/parts' },
            { label: '零件一览' },
          ],
        },
      },
      {
        path: 'parts/new',
        name: 'PartsNew',
        component: () => import('@/views/parts/PartBatchNew.vue'),
        meta: {
          title: '新建零件',
          breadcrumb: [
            { label: '订单管理', path: '/parts' },
            { label: '新建零件' },
          ],
        },
      },
      {
        // 必须放在 parts/new 之后，确保静态段优先匹配；
        // Vue Router 4 静态段优先于动态段，但显式顺序更稳。
        path: 'parts/:id(\\d+)',
        name: 'PartsDetail',
        component: () => import('@/views/parts/PartDetail.vue'),
        meta: {
          title: '零件详情',
          breadcrumb: [
            { label: '订单管理', path: '/parts' },
            { label: '零件一览', path: '/parts' },
            { label: '详情' },
          ],
        },
        props: true,
      },
      {
        path: 'workers',
        name: 'WorkerList',
        component: () => import('@/views/WorkerList.vue'),
        meta: {
          title: '工人一览',
          icon: 'User',
          breadcrumb: [
            { label: '权限管理', path: '/workers' },
            { label: '工人一览' },
          ],
        },
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '首页', icon: 'House', breadcrumb: [{ label: '首页' }] },
      },
    ],
  },
  // 工位扫码台：脱离 MainLayout，整页占屏
  {
    path: '/scan',
    children: [
      { path: '', redirect: '/scan/badge' },
      {
        path: 'badge',
        name: 'ScanBadge',
        component: () => import('@/views/scan/ScanBadgeGate.vue'),
        meta: { title: '扫码台 · 工牌识别' },
      },
      {
        path: 'action',
        name: 'ScanAction',
        component: () => import('@/views/scan/ScanActionPicker.vue'),
        meta: { title: '扫码台 · 操作选择' },
      },
      {
        path: 'parts',
        name: 'ScanParts',
        component: () => import('@/views/scan/ScanPartsWork.vue'),
        meta: { title: '扫码台 · 扫码报工' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router