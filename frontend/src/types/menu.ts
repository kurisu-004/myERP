/** 后端 MenuNodeOut 对齐 */
export interface MenuNode {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  parent_id: string | null
  code: string                    // 稳定业务键（如 'parts_list'）；路由 meta.menuCode 与之对齐
  title: string
  path: string | null             // null = 分组节点（前端渲染 <el-sub-menu>）
  icon: string | null              // Element-Plus 图标组件名（如 'House'）
  sort_order: number
  children: MenuNode[]
}