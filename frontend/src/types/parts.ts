export type PartCategory = '紧固件' | '轴承' | '传动件' | '电气件' | '油液' | '其他'
export type PartStatus = '启用' | '停用'
export type WarehouseStatus = '未入库' | '部分入库' | '已入库'

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