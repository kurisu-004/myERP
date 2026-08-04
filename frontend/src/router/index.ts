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
    /** 该路由的访问条件：用户只要拥有任一列出的角色即可进入，无需 menuCode 命中。
     *  用例：工位扫码台（/scan/*）—— SHELF_ACCOUNT 业务上必须能进，但 SHELF_ACCOUNT
     *  的菜单树不含 scan_badge。allowRoles 检查在 menuCode 检查之前触发。 */
    allowRoles?: string[]
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
        meta: { title: '零件详情', breadcrumb: [{ label: '订单管理', path: '/parts' }, { label: '零件一览', path: '/parts' }, { label: '详情' }] },
        props: true,
      },
      {
        path: 'inspection/pending',
        name: 'InspectionPending',
        component: () => import('@/views/inspection/InspectionPending.vue'),
        meta: {
          title: '待品检',
          icon: 'CircleCheck',
          menuCode: 'inspection_pending',
          breadcrumb: [{ label: '订单管理', path: '/parts' }, { label: '待品检' }],
        },
      },
      {
        // PR-M 2026-08-04 「返修接收」：订单管理子菜单，权限 MANAGER+CLERK+INSPECTOR
        path: 'repair/receive',
        name: 'RepairReceive',
        component: () => import('@/views/repair/RepairReceive.vue'),
        meta: {
          title: '返修接收',
          icon: 'Tools',
          menuCode: 'repair_receive',
          breadcrumb: [
            { label: '订单管理', path: '/parts' },
            { label: '返修接收' },
          ],
        },
      },
      {
        // 2026-07-14：待编程一览（status=PROGRAMMING），CNC 编程员专属页。
        // 侧栏作为顶级菜单渲染（t_menu.parent_id IS NULL）；权限通过 menuCode 守卫。
        path: 'cnc/pending',
        name: 'PendingProgramming',
        component: () => import('@/views/cnc/PendingProgrammingList.vue'),
        meta: {
          title: '待编程一览',
          icon: 'Cpu',
          menuCode: 'pending_programming',
          breadcrumb: [{ label: '待编程一览' }],
        },
      },
      {
        // 2026-07-16：commit 8 — /outsource 老入口重定向到 /outsource/companies
        path: '/outsource',
        redirect: '/outsource/companies',
      },
      {
        // 2026-07-16：外协厂一览（MANAGER + CLERK，commit 8 后叶子挂分组）
        path: 'outsource/companies',
        name: 'OutsourceCompaniesList',
        component: () => import('@/views/outsource/OutsourceList.vue'),
        meta: {
          title: '外协厂一览',
          icon: 'OfficeBuilding',
          menuCode: 'outsource_companies_list',
          breadcrumb: [{ label: '外协管理' }, { label: '外协厂一览' }],
        },
      },
      {
        // 2026-07-16：报价一览（MANAGER + CLERK）
        path: 'outsource/quotes',
        name: 'OutsourceQuoteList',
        component: () => import('@/views/outsource/OutsourceQuoteList.vue'),
        meta: {
          title: '报价一览',
          icon: 'Document',
          menuCode: 'outsource_quotes_list',
          breadcrumb: [{ label: '外协管理' }, { label: '报价一览' }],
        },
      },
      {
        // 2026-07-16：外协发送/接收（MANAGER + CLERK；合并原 send + receive）
        path: 'outsource/send-receive',
        name: 'OutsourceSendReceive',
        component: () => import('@/views/outsource/OutsourceSendReceive.vue'),
        meta: {
          title: '外协发送/接收',
          icon: 'Promotion',
          menuCode: 'outsource_send_receive_list',
          breadcrumb: [{ label: '外协管理' }, { label: '外协发送/接收' }],
        },
      },
      {
        // 2026-07-16 兼容：旧路径 → 重定向到新页面，URL ?tab= 同步
        path: 'outsource/send',
        redirect: { path: '/outsource/send-receive', query: { tab: 'sendable' } },
      },
      {
        path: 'outsource/receive',
        redirect: { path: '/outsource/send-receive', query: { tab: 'receiving' } },
      },
      {
        // 2026-07-28：外协对账一览（按公司聚合 SENT_TO_OUTSOURCE 事件）
        path: 'outsource/companies/:id/sent-parts',
        name: 'OutsourceCompanySentParts',
        component: () => import('@/views/outsource/OutsourceCompanySentParts.vue'),
        meta: {
          title: '外协对账',
          // 不暴露为独立菜单；通过公司列表的「对账」链接进入。
          menuCode: 'outsource_companies_list',
          breadcrumb: [
            { label: '外协管理' },
            { label: '外协厂一览', path: '/outsource/companies' },
            { label: '外协对账' },
          ],
        },
      },
      {
        // 2026-07-30：装配件一览退役，合并到零件一览
        path: 'assemblies',
        redirect: '/parts',
      },
      {
        path: 'delivery-notes',
        name: 'DeliveryNoteList',
        component: () => import('@/views/delivery/DeliveryNoteList.vue'),
        meta: {
          title: '送货单',
          icon: 'Document',
          menuCode: 'delivery_notes_manage',
          breadcrumb: [{ label: '订单管理', path: '/parts' }, { label: '送货单' }],
        },
      },
      {
        path: 'delivery-notes/:id(\\d+)',
        name: 'DeliveryNoteDetail',
        component: () => import('@/views/delivery/DeliveryNoteDetail.vue'),
        meta: {
          title: '送货单详情',
          menuCode: 'delivery_notes_manage',
          breadcrumb: [
            { label: '订单管理', path: '/parts' },
            { label: '送货单', path: '/delivery-notes' },
            { label: '详情' },
          ],
        },
      },
      {
        path: 'assemblies/:id(\\d+)',
        name: 'AssemblyDetail',
        component: () => import('@/views/assemblies/AssemblyDetail.vue'),
        meta: { title: '装配件详情', breadcrumb: [{ label: '订单管理', path: '/assemblies' }, { label: '装配件一览', path: '/assemblies' }, { label: '详情' }] },
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
      {
        path: 'statistics',
        name: 'ProductionStats',
        component: () => import('@/views/statistics/ProductionStats.vue'),
        meta: {
          title: '生产统计',
          icon: 'DataAnalysis',
          menuCode: 'production_stats',
          breadcrumb: [{ label: '生产统计' }],
        },
      },
      {
        path: 'customers',
        name: 'CustomerList',
        component: () => import('@/views/customers/CustomerList.vue'),
        meta: {
          title: '客户一览',
          icon: 'Connection',
          menuCode: 'customers_list',
          breadcrumb: [{ label: '客户管理', path: '/customers' }, { label: '客户一览' }],
        },
      },
      {
        path: 'applicants',
        name: 'ApplicantList',
        component: () => import('@/views/applicants/ApplicantList.vue'),
        meta: {
          title: '申请人一览',
          icon: 'User',
          menuCode: 'applicants_list',
          breadcrumb: [{ label: '客户管理', path: '/applicants' }, { label: '申请人一览' }],
        },
      },
      {
        path: 'settings/work-types',
        name: 'WorkTypeList',
        component: () => import('@/views/settings/WorkTypeList.vue'),
        meta: { title: '工种管理', menuCode: 'work_types_list', breadcrumb: [{ label: '设置', path: '/settings/work-types' }, { label: '工种管理' }] },
      },
      {
        path: 'settings/processes',
        name: 'ProcessList',
        component: () => import('@/views/settings/ProcessList.vue'),
        meta: { title: '工序管理', menuCode: 'processes_list', breadcrumb: [{ label: '设置', path: '/settings/work-types' }, { label: '工序管理' }] },
      },
      {
        path: 'settings/work-type-processes',
        name: 'WorkTypeProcess',
        component: () => import('@/views/settings/WorkTypeProcess.vue'),
        meta: { title: '工种-工序映射', menuCode: 'work_type_processes_list', breadcrumb: [{ label: '设置', path: '/settings/work-types' }, { label: '工种-工序映射' }] },
      },
    ],
  },
  // 工位扫码台
  {
    path: '/scan',
    meta: { requireAuth: true, allowRoles: ['SHELF_ACCOUNT'] },
    children: [
      { path: '', redirect: '/scan/badge' },
      { path: 'badge', name: 'ScanBadge', component: () => import('@/views/scan/ScanBadgeGate.vue'), meta: { title: '扫码台 · 工牌识别', menuCode: 'scan_badge' } },
      { path: 'action', name: 'ScanAction', component: () => import('@/views/scan/ScanActionPicker.vue'), meta: { title: '扫码台 · 操作选择', menuCode: 'scan_badge' } },
      { path: 'pick', name: 'ScanPick', component: () => import('@/views/scan/ScanPickParts.vue'), meta: { title: '扫码台 · 选件领取', menuCode: 'scan_badge' } },
      { path: 'return', name: 'ScanReturn', component: () => import('@/views/scan/ScanReturnParts.vue'), meta: { title: '扫码台 · 选件放回', menuCode: 'scan_badge' } },
      { path: 'inspect', name: 'ScanInspect', component: () => import('@/views/scan/ScanInspectParts.vue'), meta: { title: '扫码台 · 选件送检', menuCode: 'scan_badge' } },
    ],
  },
  // 司机送货扫码台（2026-07-23）：MANAGER/INSPECTOR 的「送货」菜单入口，全屏、
  // MainLayout 之外。工牌识别 → 只有「送货司机」工种可通过 → 待送货单选择 →
  // 逐件扫描 → 确认送货（复用后端 pickup-scan / pickup）。
  {
    path: '/delivery-dispatch',
    meta: { requireAuth: true },
    children: [
      { path: '', redirect: '/delivery-dispatch/badge' },
      {
        path: 'badge',
        name: 'DispatchBadge',
        component: () => import('@/views/delivery-dispatch/DispatchBadgeGate.vue'),
        meta: { title: '送货 · 工牌识别', menuCode: 'delivery_dispatch' },
      },
      {
        path: 'notes',
        name: 'DispatchNotes',
        component: () => import('@/views/delivery-dispatch/DispatchNoteList.vue'),
        meta: { title: '送货 · 待送货单', menuCode: 'delivery_dispatch' },
      },
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

/** DFS 在用户的菜单树中找第一个有 path 的节点路径；找不到返回 null。
 *  用作 menuCode 校验失败时的降级目标：避免再次陷入相同的菜单校验循环。 */
function findFirstMenuPath(tree: MenuNode[]): string | null {
  const stack: MenuNode[] = [...tree]
  while (stack.length > 0) {
    const n = stack.pop()!
    if (n.path) return n.path
    if (n.children.length > 0) stack.push(...n.children)
  }
  return null
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

  // 2) allowRoles 短路：用户拥有任一列出的角色则直接放行，不管 menuCode。
  //    用于 SHELF_ACCOUNT → /scan/* 等"业务上必须能进但 menuCode 校验会卡住"的场景。
  const allowRoles = to.meta.allowRoles ?? []
  if (allowRoles.length > 0 && allowRoles.some((r) => hasRole(r))) {
    return next()
  }

  // 3) menuCode 校验：菜单树中存在对应 code 即放行。
  //    单一权限源。降级目标：用户菜单树中第一个可达路径；
  //    若菜单树为空（极端情况）→ /login。
  const code = to.meta.menuCode
  if (code && !treeContainsCode(menus(), code)) {
    const fallback = findFirstMenuPath(menus()) ?? '/login'
    if (fallback === to.fullPath) return next()  // 自环保护，防止未来回归
    return next(fallback)
  }

  next()
})

export default router
