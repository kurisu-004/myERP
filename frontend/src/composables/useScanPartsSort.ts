// composables/useScanPartsSort.ts
//
// 报工台三页（ScanPickParts / ScanReturnParts / ScanInspectParts）共用客户端排序。
// 替代原来后端 SQL `is_urgent DESC → planned_delivery_date ASC NULLS LAST → id DESC`
// 的硬编码承诺，引入「系统交期」硬优先级：含 system_delivery_date 的工件整体提到
// 无 system_delivery_date 的工件之前。
//
// 排序键（依次）：
//   1. is_urgent DESC                              — 保留原优先级
//   2. system_delivery_date IS NOT NULL DESC       — 新硬优先级：有系统交期的排前面
//   3. 同组内：日期 ASC NULLS LAST                  — 有 system 用 system，没用 planned
//   4. id 字符串 DESC（雪花 ID 单调递增，字典序等价于数值序）— 稳定 tie-break

import { computed, toValue, type ComputedRef, type MaybeRefOrGetter } from 'vue'

/** 排序所需的最小字段契约。PartItem / PartListItem 等都满足。 */
export interface ScanSortablePart {
  id: string
  is_urgent: boolean
  planned_delivery_date: string | null
  system_delivery_date: string | null
}

export function useScanPartsSort<T extends ScanSortablePart>(
  parts: MaybeRefOrGetter<T[]>,
): ComputedRef<T[]> {
  return computed(() => {
    const list = toValue(parts)
    // 复制后排序，避免原地变更 ref 持有的数组触发循环响应
    return [...list].sort(compareScanParts)
  })
}

function compareScanParts<T extends ScanSortablePart>(a: T, b: T): number {
  // 1. is_urgent DESC
  const urgent = Number(!!b.is_urgent) - Number(!!a.is_urgent)
  if (urgent !== 0) return urgent

  // 2. has system_delivery_date DESC（硬优先级）
  const aHasSys = a.system_delivery_date != null
  const bHasSys = b.system_delivery_date != null
  const sys = Number(bHasSys) - Number(aHasSys)
  if (sys !== 0) return sys

  // 3. 同组内日期 ASC NULLS LAST（用 group 各自的日期字段）
  const ad = aHasSys ? a.system_delivery_date : a.planned_delivery_date
  const bd = bHasSys ? b.system_delivery_date : b.planned_delivery_date
  if (ad == null && bd == null) return idDesc(a.id, b.id)
  if (ad == null) return 1
  if (bd == null) return -1
  const diff = new Date(ad).getTime() - new Date(bd).getTime()
  if (diff !== 0) return diff
  return idDesc(a.id, b.id)
}

/**
 * 雪花 ID 字符串降序。雪花 ID 单调递增，字符串字典序等价于数值序，
 * 用字符串比较可避免 `Number()` 转换时的精度丢失（Number.MAX_SAFE_INTEGER ≈ 9e15）。
 */
function idDesc(a: string, b: string): number {
  if (a === b) return 0
  return a < b ? 1 : -1
}