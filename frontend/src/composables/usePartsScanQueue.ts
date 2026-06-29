// composables/usePartsScanQueue.ts
//
// 工位扫码台"待提交工件队列"：单例 ref<PartEntry[]>。
// - addOrIgnore(code)：去重后入队，立即触发 GET /parts/by-serial/{code} 查零件信息。
// - remove(uid)：移除单行。
// - reset()：清空队列（保留 worker / action）。
// - submit(badgeCode, action)：按 action 类型逐行调用 pickUpPart 或 scanPart。
//
// 移植自 BarcodeWorkModal.vue：
//   - 去重判断 parts.value.some((p) => p.drawingCode === c)
//   - 顺序提交 + 每行重赋数组触发响应式 parts.value = [...parts.value]

import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getPartBySerial,
  pickUpPart,
  scanPart,
  type PartItem,
} from '@/api/parts'
import type { WorkAction } from '@/composables/useScanSession'

export type PartEntryPhase = 'loading' | 'pending' | 'success' | 'error'

export interface PartEntry {
  uid: string
  drawingCode: string
  /** 提交前是 'loading' / 'pending'；提交过程中及之后是 'success' / 'error'。 */
  phase: PartEntryPhase
  part?: PartItem
  error?: string
}

const parts = ref<PartEntry[]>([])

function makeUid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `uid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function bump(): void {
  // 触发响应式：数组里的对象属性变更不会自动反映到视图中，
  // 重新赋一次数组引用让 Vue 重新 diff。
  parts.value = [...parts.value]
}

export function usePartsScanQueue() {
  const total: ComputedRef<number> = computed(() => parts.value.length)
  const submittedCount: ComputedRef<number> = computed(
    () => parts.value.filter((p) => p.phase === 'success' || p.phase === 'error').length,
  )
  const successCount: ComputedRef<number> = computed(
    () => parts.value.filter((p) => p.phase === 'success').length,
  )
  const failCount: ComputedRef<number> = computed(
    () => parts.value.filter((p) => p.phase === 'error').length,
  )

  async function addOrIgnore(rawCode: string): Promise<void> {
    const c = rawCode.trim()
    if (!c) return
    if (parts.value.some((p) => p.drawingCode === c)) {
      ElMessage.warning(`已扫过: ${c}`)
      return
    }
    const entry: PartEntry = { uid: makeUid(), drawingCode: c, phase: 'loading' }
    parts.value = [...parts.value, entry]
    try {
      const part = await getPartBySerial(c)
      entry.part = part
      entry.phase = 'pending'
    } catch (e) {
      entry.phase = 'error'
      entry.error = (e as Error).message ?? '未找到零件'
    }
    bump()
  }

  function remove(uid: string): void {
    parts.value = parts.value.filter((p) => p.uid !== uid)
  }

  function reset(): void {
    parts.value = []
  }

  async function submit(badgeCode: string, action: WorkAction): Promise<void> {
    if (parts.value.length === 0) return
    for (const entry of parts.value) {
      // 只对加载成功的零件发提交（loading 状态说明上一次查无此码）
      if (entry.phase !== 'pending') continue
      try {
        if (action === 'PICK_UP') {
          entry.part = await pickUpPart({
            drawing_code: entry.drawingCode,
            badge_code: badgeCode,
          })
        } else if (action === 'RETURN') {
          entry.part = await scanPart({
            drawing_code: entry.drawingCode,
            event_type: 'RETURNED',
          })
        } else {
          entry.part = await scanPart({
            drawing_code: entry.drawingCode,
            event_type: 'INSPECTED',
          })
        }
        entry.phase = 'success'
      } catch (e) {
        entry.phase = 'error'
        entry.error = (e as Error).message ?? '提交失败'
      }
      bump()
    }
  }

  return {
    parts: parts as Ref<PartEntry[]>,
    total,
    submittedCount,
    successCount,
    failCount,
    addOrIgnore,
    remove,
    reset,
    submit,
  }
}