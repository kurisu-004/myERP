// 2026-07-17：货架↔工序 双向 reactive 过滤 composable
//
// 用法：
//   const { filteredShelves, filteredProcesses, load, loaded } = useShelfProcessFilter(
//     shelves,       // Ref<readonly Shelf[]>
//     processes,     // Ref<readonly Process[]>
//     shelfId,       // Ref<string | null>
//     processId,     // Ref<string | null>
//   )
//   await load()   // 弹窗打开时调用一次
//
// 后端走 `GET /shelves/processes` 一次取所有 active 映射（避免 N+1）。
// watch 会自动在 shelfId / processId 变化时双向 refilter，
// 并在选了不兼容的对端时清空对端 + ElMessage.warning 提示。

import { computed, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getAllShelfProcessMappings } from '@/api/shelves'

/**
 * 最小接口：composable 只关心 id（雪花 ID 字符串）。
 * 调用方可以传 Shelf[] / Process[] 或任何带 `{id: string}` 字段的对象。
 */
export interface Identifiable {
  id: string
}

export function useShelfProcessFilter<
  S extends Identifiable,
  P extends Identifiable,
>(
  allShelves: Ref<readonly S[]>,
  allProcesses: Ref<readonly P[]>,
  shelfId: Ref<string | null>,
  processId: Ref<string | null>,
) {
  // shelfId (str) → Set<processId (str)>
  const mapping = ref<Map<string, Set<string>>>(new Map())
  const loaded = ref(false)
  const loading = ref(false)

  /** 弹窗打开时调用一次：从后端一次性拉全量映射 */
  async function load(): Promise<void> {
    if (loading.value) return
    loading.value = true
    try {
      const r = await getAllShelfProcessMappings()
      const m = new Map<string, Set<string>>()
      for (const item of r.items) {
        m.set(item.shelf_id, new Set(item.process_ids))
      }
      mapping.value = m
      loaded.value = true
    } catch (e) {
      // 后端失败：loaded 保持 false，filteredXxx 直接返回全量（兜底）
      // eslint-disable-next-line no-console
      console.error('useShelfProcessFilter.load failed', e)
      loaded.value = false
    } finally {
      loading.value = false
    }
  }

  /** 给定 shelfId 返回 Set<processId>；无映射 → null */
  function processesForShelf(sid: string | null | undefined): Set<string> | null {
    if (!sid) return null
    return mapping.value.get(sid) ?? null
  }

  /** 选了某 shelf → 过滤可选 process */
  const filteredProcesses = computed<readonly P[]>(() => {
    if (!loaded.value || !shelfId.value) return allProcesses.value
    const allowed = processesForShelf(shelfId.value)
    if (!allowed) return allProcesses.value
    return allProcesses.value.filter((p) => allowed.has(p.id))
  })

  /** 选了某 process → 过滤可选 shelf */
  const filteredShelves = computed<readonly S[]>(() => {
    if (!loaded.value || !processId.value) return allShelves.value
    return allShelves.value.filter(
      (s) => mapping.value.get(s.id)?.has(processId.value as string),
    )
  })

  // 选了 process，但当前 shelf 不支持 → 清空 shelf + 提示
  watch(processId, (pid) => {
    if (!pid || !shelfId.value || !loaded.value) return
    const allowed = processesForShelf(shelfId.value)
    if (!allowed || !allowed.has(pid)) {
      shelfId.value = null
      ElMessage.warning('已清空货架选择：当前货架不支持该工序')
    }
  })

  // 选了 shelf，但当前 process 不在它的映射里 → 清空 process（静默，避免双向 toast）
  watch(shelfId, (sid) => {
    if (!sid || !processId.value || !loaded.value) return
    const allowed = processesForShelf(sid)
    if (!allowed || !allowed.has(processId.value)) {
      processId.value = null
    }
  })

  return {
    filteredShelves,
    filteredProcesses,
    load,
    loaded,
    loading,
    /** 给工具方法用：当前 shelf 是否支持某 process（不触发 reactive） */
    processesForShelf,
  }
}