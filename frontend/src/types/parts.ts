export type PartCategory = '紧固件' | '轴承' | '传动件' | '电气件' | '油液' | '其他'
export type PartStatus = '启用' | '停用'
export type WarehouseStatus = '未入库' | '部分入库' | '已入库'

/** 后端订单状态枚举（数据大屏用） */
export type OrderStatus =
  | 'PENDING'
  | 'READY'
  | 'IN_PROCESS'
  | 'INSPECTION'
  | 'READY_TO_SHIP'
  | 'DELIVERED'
  | 'REPAIRING'
  | 'COMPLETED'
  | 'CANCELLED'

export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  PENDING: '待生产',
  READY: '就绪',
  IN_PROCESS: '生产中',
  INSPECTION: '待品检',
  READY_TO_SHIP: '待送货',
  DELIVERED: '已送货',
  REPAIRING: '返修中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

export const ORDER_STATUS_TAG_TYPE: Record<OrderStatus, 'info' | 'warning' | 'success' | 'danger' | 'primary'> = {
  PENDING: 'info',
  READY: 'warning',
  IN_PROCESS: 'primary',
  INSPECTION: 'warning',
  READY_TO_SHIP: 'warning',
  DELIVERED: 'success',
  REPAIRING: 'danger',
  COMPLETED: 'success',
  CANCELLED: 'info',
}

export type PartSortKey =
  | 'PLANNED_DELIVERY_DATE'
  | 'REQUEST_DATE'
  | 'CREATED_AT'

export type SortDir = 'ASC' | 'DESC'

/** 后端 PartEventType 枚举 */
export type PartEventType =
  | 'CREATED'
  | 'RELEASED'
  | 'PICKED_UP'
  | 'RETURNED'
  | 'INSPECTED'
  | 'STATUS_CHANGED'
  | 'REPAIR_STARTED'
  | 'REPAIR_COMPLETED'
  | 'CANCELLED'
  | 'COMPLETED'

export const PART_EVENT_LABEL: Record<PartEventType, string> = {
  CREATED: '创建',
  RELEASED: '开始生产',
  PICKED_UP: '领取',
  RETURNED: '归还',
  INSPECTED: '送检',
  STATUS_CHANGED: '状态变更',
  REPAIR_STARTED: '开始返修',
  REPAIR_COMPLETED: '返修完成',
  CANCELLED: '取消',
  COMPLETED: '完成',
}

export const PART_EVENT_TAG_TYPE: Record<PartEventType, 'primary' | 'success' | 'warning' | 'info' | 'danger'> = {
  CREATED: 'primary',
  RELEASED: 'success',
  PICKED_UP: 'warning',
  RETURNED: 'info',
  INSPECTED: 'primary',
  STATUS_CHANGED: 'info',
  REPAIR_STARTED: 'danger',
  REPAIR_COMPLETED: 'success',
  CANCELLED: 'danger',
  COMPLETED: 'success',
}

/** 扫码台允许的 event_type 子集 */
export const SCAN_EVENT_TYPE_OPTIONS: PartEventType[] = ['PICKED_UP', 'RETURNED', 'INSPECTED']

export interface PartItem {
  id: number
  /** 内部编号（HSH+年月日+类型） */
  internalNo: string
  /** 订单编号（外部单据号） */
  orderNo: string
  /** 申请部门 */
  department: string
  /** 图号 */
  drawingNo: string
  /** 品名 / 零件名称 */
  partName: string
  /** 分类 */
  category: PartCategory
  /** 规格 */
  spec: string
  /** 单位 */
  unit: string
  /** 数量 */
  qty: number
  /** 单价 */
  unitPrice: number
  /** 加工单价（慢丝/线切割等） */
  processPrice: number
  /** 总价 = 数量 * 单价 */
  totalPrice: number
  /** 供应商 */
  supplier: string
  /** 请购日期 YYYY-MM-DD */
  requestDate: string
  /** 计划交期 YYYY-MM-DD */
  planDate: string
  /** 送检日期 YYYY-MM-DD */
  inspectDate: string
  /** 入库情况 */
  warehouseStatus: WarehouseStatus
  /** 返修日期 / 备注 */
  reworkDate: string
  /** 启用 / 停用 */
  status: PartStatus
  /** 更新时间 YYYY-MM-DD HH:mm */
  updatedAt: string
}

export interface PartSearchForm {
  internalNo: string
  orderNo: string
  drawingNo: string
  partName: string
  category: PartCategory | ''
  warehouseStatus: WarehouseStatus | ''
  status: PartStatus | ''
}

export interface OptionItem<T = string> {
  value: T
  label: string
}