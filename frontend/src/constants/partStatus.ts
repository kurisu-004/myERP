// 工单状态 (PartStatus) 枚举的中文 label 与 el-tag type 映射。
//
// 用途：统计页面（车间生产总览 / 工人详情）展示当前状态分布、参与工单状态 tag。
// 注意：与 types/parts.ts::ORDER_STATUS_LABEL 不同——本文件用于「生产统计」语义下的
// 中文文案（如「待处理」「CNC 编程」「外协」），而 PartsList 用「待生产/编程中」等
// 偏订单管理语义。两者并存不重复：project 范围内的 status 枚举值稳定，仅文案表述
// 不同。

import type { OrderStatus } from '@/types/parts'

export const STATUS_LABEL: Record<OrderStatus, string> = {
  PENDING: '待处理',
  PROGRAMMING: 'CNC 编程',
  IN_PROCESS: '在制',
  INSPECTION: '品检中',
  READY_TO_SHIP: '待发货',
  DELIVERED: '已发货',
  REPAIRING: '返修中',
  OUTSOURCE: '外协',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

export const STATUS_TAG_TYPE: Record<OrderStatus, 'info' | 'primary' | 'success' | 'warning' | 'danger'> = {
  PENDING: 'info',
  PROGRAMMING: 'primary',
  IN_PROCESS: 'primary',
  INSPECTION: 'warning',
  READY_TO_SHIP: 'warning',
  DELIVERED: 'success',
  REPAIRING: 'danger',
  OUTSOURCE: 'primary',
  COMPLETED: 'success',
  CANCELLED: 'info',
}