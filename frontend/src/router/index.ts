import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import './types'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/parts',
    children: [
      {
        path: 'parts',
        name: 'PartsList',
        component: () => import('@/views/parts/PartsList.vue'),
        meta: { title: '零件一览', icon: 'Box', breadcrumb: ['基础数据', '零件一览'] },
      },
      {
        path: 'parts/new',
        name: 'PartsNew',
        component: () => import('@/views/parts/PartsForm.vue'),
        meta: { title: '新增零件', breadcrumb: ['基础数据', '零件一览', '新增零件'] },
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '首页', icon: 'House', breadcrumb: ['首页'] },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
