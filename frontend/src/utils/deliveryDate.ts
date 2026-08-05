/**
 * 计划交期缓冲天数。
 *
 * 2026-08-05 起前端不再减缓冲，直接显示真实计划交期，与后端打印背面
 *（service/printing.py 已无 buffer）对齐；常量保留为 0，以便将来需要时恢复。
 */
export const DELIVERY_DATE_BUFFER_DAYS = 0

type DeliveryDate = string | null | undefined

/** 把 ISO 日期字符串向过去推 N 天，返回新 ISO 日期字符串。空值 → 空串。 */
function shiftIsoDate(s: string, days: number): string {
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return ''
  d.setDate(d.getDate() - days)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

/** 缓冲后的日期；空值 → 空串。 */
export function bufferedDeliveryDate(s: DeliveryDate): string {
  if (!s) return ''
  return shiftIsoDate(s, DELIVERY_DATE_BUFFER_DAYS)
}

/** 扫码三页显示交期：缓冲后的 MM/DD。 */
export function formatDeliveryDate(s: DeliveryDate): string {
  const buffered = bufferedDeliveryDate(s)
  if (!buffered) return ''
  return buffered.slice(5).replace(/-/g, '/')
}

/** 基于缓冲后日期计算剩余天数文案。空值→空；过期→'已逾期N天'；今天→'今天到期'；≤3 天→'N天后到期'。 */
export function deliveryDaysLeftText(s: DeliveryDate): string {
  const buffered = bufferedDeliveryDate(s)
  if (!buffered) return ''
  const target = new Date(buffered)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((target.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return `已逾期${Math.abs(diff)}天`
  if (diff === 0) return '今天到期'
  if (diff <= 3) return `${diff}天后到期`
  return ''
}

/** 基于缓冲后日期返回样式类：'overdue' / 'due-soon' / ''。 */
export function deliveryUrgencyClass(s: DeliveryDate): '' | 'overdue' | 'due-soon' {
  const buffered = bufferedDeliveryDate(s)
  if (!buffered) return ''
  const target = new Date(buffered)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((target.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'overdue'
  if (diff <= 3) return 'due-soon'
  return ''
}

/** 基于缓冲后日期返回 Element Plus Tag 类型。ScanPickParts 专用。 */
export function deliveryUrgencyTag(s: DeliveryDate): 'danger' | 'warning' | 'info' {
  const buffered = bufferedDeliveryDate(s)
  if (!buffered) return 'info'
  const target = new Date(buffered)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((target.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'danger'
  if (diff <= 3) return 'warning'
  return 'info'
}

/** Dashboard 货架卡片专用：缓冲后短日期 MM-DD；空值 → '-'。 */
export function formatDashboardDeliveryDate(s: DeliveryDate): string {
  const buffered = bufferedDeliveryDate(s)
  if (!buffered) return '-'
  return buffered.slice(5)
}
