export type PartCategory = '紧固件' | '轴承' | '传动件' | '电气件' | '油液' | '其他'
export type PartStatus = '启用' | '停用'
export type WarehouseStatus = '未入库' | '部分入库' | '已入库'

/** 后端订单状态枚举（数据大屏用） */
export type OrderStatus =
  | 'PENDING'
  | 'PROGRAMMING'
  | 'IN_PROCESS'
  | 'INSPECTION'
  | 'READY_TO_SHIP'
  | 'DELIVERED'
  | 'REPAIRING'
  | 'OUTSOURCE'
  | 'COMPLETED'
  | 'CANCELLED'

export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  PENDING: '待生产',
  PROGRAMMING: '编程中',
  IN_PROCESS: '生产中',
  INSPECTION: '待品检',
  READY_TO_SHIP: '待送货',
  DELIVERED: '已送货',
  REPAIRING: '返修中',
  OUTSOURCE: '外协中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

export const ORDER_STATUS_TAG_TYPE: Record<OrderStatus, 'info' | 'warning' | 'success' | 'danger' | 'primary'> = {
  PENDING: 'info',
  PROGRAMMING: 'warning',
  IN_PROCESS: 'primary',
  INSPECTION: 'warning',
  READY_TO_SHIP: 'primary',
  DELIVERED: 'success',
  REPAIRING: 'danger',
  OUTSOURCE: 'warning',
  COMPLETED: 'success',
  CANCELLED: 'info',
}

export type PartSortKey =
  | 'PLANNED_DELIVERY_DATE'
  | 'REQUEST_DATE'
  | 'SYSTEM_DELIVERY_DATE'
  | 'CREATED_AT'
  | 'SERIAL_NO'
  | 'DRAWING_NO'
  | 'NAME'
  | 'ORDER_NO'
  | 'QUANTITY'
  | 'UNIT_PRICE'
  | 'TOTAL_PRICE'

export type SortDir = 'ASC' | 'DESC'

/**
 * `el-table` 列 `prop` → 后端 `PartSortKey` 映射。
 * PartsList / DeliveryNoteNew 共用；列头点击 → onSortChange → 触发服务端排序。
 * 命名对应 `model/enums.py::PartSortKey`。
 */
export const PART_SORT_PROP_MAP: Record<string, PartSortKey> = {
  serial_no: 'SERIAL_NO',
  drawing_no: 'DRAWING_NO',
  name: 'NAME',
  planned_delivery_date: 'PLANNED_DELIVERY_DATE',
  request_date: 'REQUEST_DATE',
  system_delivery_date: 'SYSTEM_DELIVERY_DATE',
  order_no: 'ORDER_NO',
  quantity: 'QUANTITY',
  unit_price: 'UNIT_PRICE',
  total_price: 'TOTAL_PRICE',
}

/** PartSortKey 合法值集合（用于持久化恢复时收敛到合法值）。 */
export const PART_SORT_KEY_SET: ReadonlySet<PartSortKey> =
  new Set(Object.values(PART_SORT_PROP_MAP))
/** PartSortKey → 列 prop 名（用于 default-sort / elTableRef.sort()）。 */
export const PART_SORT_KEY_TO_PROP: Record<PartSortKey, string> =
  Object.fromEntries(
    Object.entries(PART_SORT_PROP_MAP).map(([prop, key]) => [key, prop]),
  ) as Record<PartSortKey, string>

/** 列表展示用窄出参（与 PartItem 不同，无 holder/next_process/assembly_id）。 */
export interface PartListItem {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增（后端 SQLAlchemy version_id_col）；
   *  当前端暂不消费，后续可用于冲突检测。 */
  version: number
  serial_no: string | null
  name: string
  drawing_no: string
  /** 申请人姓名快照 */
  applicant_name: string | null
  quantity: number
  /** 单价 */
  unit_price: number
  /** 总价 = quantity * unit_price（2026-07-24 新增；后端落库字段，UI 直接展示） */
  total_price: number
  /** 请购日期 */
  request_date: string
  planned_delivery_date: string
  actual_delivery_date: string | null
  is_urgent: boolean
  status: OrderStatus
  /** PR-F 2026-07-17：送货单字段 */
  order_no: string | null
  system_delivery_date: string | null
  /** 已送数量：未软删批次中 status ∈ (DELIVERED, COMPLETED) 的 quantity 之和；装配件行恒为 null */
  delivered_quantity?: number | null
  note: string | null
  customer_name: string | null
  parent_customer_name: string | null
  customer_path: string | null
  /** PR-G 2026-07-22：所属送货单 id（NULL = 未开单）；列表浅蓝染色依据 */
  delivery_note_id: string | null
  location: string | null
  shelf_code: string | null
  worker_name: string | null
  /** holder 是外协公司时的公司名（OUTSOURCE_COMPANY 位置带出） */
  outsource_company_name?: string | null
  /** 所在位置（2026-07-11 接入）：装配体子件表用，PRODUCTION_SHELF→'货架 A-01'；
   * INSPECTION_SHELF→'品检 A-01'；WORKER→'工人 张三'；OFFICE→'编程员持有'。 */
  current_holder_display?: string | null
  /** PR-H 2026-07-28：下一工序 id（NULL = 未设置；新建外协报价 picker 自动填工序用） */
  next_process_id: string | null
  /** PR-H 2026-07-28：下一工序名 */
  next_process_name: string | null
  /** 2026-07-29 PR-fix-0.2.0 批次化字段：仅 /outsource-quotes/quotable-parts 走批次时填充 */
  batch_id?: string | null
  /** 2026-07-29 PR-fix-0.2.0 批次化字段：批次号（per-part 递增） */
  batch_no?: number | null
  /** 2026-07-29 PR-fix-0.2.0 批次化字段：批次数量（picker 选中后可挂在报价上） */
  batch_quantity?: number | null
  /** 2026-07-30：列表行类型（零件一览合并装配件） */
  row_type?: 'PART' | 'ASSEMBLY'
  /** 2026-07-30：树表用，是否有子件（仅装配件行） */
  has_children?: boolean
  /** 2026-07-30：子件数量（仅装配件行） */
  child_count?: number | null
  /** 2026-07-30：创建时间（装配件行带出） */
  created_at?: string | null
  /** PR-M 2026-08-04：是否经历过返修（用于列表行展示「返修」el-tag） */
  has_been_repaired?: boolean
  /** C2 2026-08-05：装配件携带的「命中子件」；仅当 next_process_ids / locations /
   *  holder_ids 筛选激活时填充。其余情况为 null。前端 loadChildren 优先消费。 */
  matched_children?: PartListItem[] | null
}

/** 零件一览「所在位置」树节点（GET /parts/location-tree）。 */
export interface LocationTreeNode {
  id: string            // 父节点=PartLocation 值；叶子=holder 雪花 ID 字符串
  name: string
  location: string | null
  children: LocationTreeNode[]
}
/** 行类型筛选：全部 / 仅零件 / 仅装配件 */
export type PartRowTypeFilter = 'ALL' | 'PART' | 'ASSEMBLY'

/** 后端 PartEventType 枚举 */
export type PartEventType =
  | 'CREATED'
  | 'RELEASED'
  | 'SENT_TO_PROGRAMMING'
  | 'CNC_RELEASED'
  | 'PLACED_ON_SHELF'
  | 'PICKED_UP'
  | 'RETURNED'
  | 'INSPECTED'
  | 'INSPECTION_FAILED'
  | 'STATUS_CHANGED'
  | 'REPAIR_STARTED'
  | 'REPAIR_COMPLETED'
  | 'SENT_TO_OUTSOURCE'
  | 'RECEIVED_FROM_OUTSOURCE'
  | 'QUOTE_CREATED'
  | 'QUOTE_APPROVED'
  | 'CANCELLED'
  | 'COMPLETED'
  | 'SPLIT'
  | 'RECALLED'   // 2026-08-05 召回：ON_SHELF/PROGRAMMING → PENDING/PROGRAMMING

export const PART_EVENT_LABEL: Record<PartEventType, string> = {
  CREATED: '创建',
  RELEASED: '开始生产',
  SENT_TO_PROGRAMMING: '发送至CNC编程',
  CNC_RELEASED: 'CNC下发生产',
  PLACED_ON_SHELF: '放置到货架',
  PICKED_UP: '领取',
  RETURNED: '归还',
  INSPECTED: '送检',
  INSPECTION_FAILED: '品检打回',
  STATUS_CHANGED: '状态变更',
  REPAIR_STARTED: '开始返修',
  REPAIR_COMPLETED: '返修完成',
  SENT_TO_OUTSOURCE: '发送至外协',
  RECEIVED_FROM_OUTSOURCE: '外协回收',
  QUOTE_CREATED: '创建外协报价',
  QUOTE_APPROVED: '报价审核通过',
  CANCELLED: '取消',
  COMPLETED: '完成',
  SPLIT: '批次拆分',
  RECALLED: '召回',
}

export const PART_EVENT_TAG_TYPE: Record<PartEventType, 'primary' | 'success' | 'warning' | 'info' | 'danger'> = {
  CREATED: 'primary',
  RELEASED: 'success',
  SENT_TO_PROGRAMMING: 'warning',
  CNC_RELEASED: 'success',
  PLACED_ON_SHELF: 'success',
  PICKED_UP: 'warning',
  RETURNED: 'info',
  INSPECTED: 'primary',
  INSPECTION_FAILED: 'danger',
  STATUS_CHANGED: 'info',
  REPAIR_STARTED: 'danger',
  REPAIR_COMPLETED: 'success',
  SENT_TO_OUTSOURCE: 'warning',
  RECEIVED_FROM_OUTSOURCE: 'success',
  QUOTE_CREATED: 'info',
  QUOTE_APPROVED: 'success',
  CANCELLED: 'danger',
  COMPLETED: 'success',
  SPLIT: 'info',
  RECALLED: 'warning',
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