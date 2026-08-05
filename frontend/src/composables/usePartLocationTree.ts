// usePartLocationTree.ts
//
// 「零件一览」所在位置树（5 个 PartLocation 大类 + 具体 holder 叶子）加载与解析工具。
//
// 设计意图：
// - 数据只一次性加载（模块级 Promise 缓存 + 组件级 loading 标志），变更低频；
// - 调用方用 `load()` 触发加载；已加载 / 加载中则直接返回同一 Promise，幂等；
// - `splitLocationSelection` 把 el-tree-select 的多选值按「大类」与「具体 holder」拆开，
//   对应后端的 `locations`（PartLocation 值）与 `holder_ids`（雪花 ID 字符串）两组查询参数。
//
// 错误提示沿用项目惯例：Element Plus 全局 ElMessage。

import { ref, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getPartLocationTree } from '@/api/parts'
import type { LocationTreeNode } from '@/types/parts'

/** 5 个 PartLocation 大类值，用于把 tree-select 选中值拆成 locations / holderIds。 */
export const PART_LOCATION_VALUES: readonly string[] = [
  'OFFICE',
  'PRODUCTION_SHELF',
  'WORKER',
  'INSPECTION_SHELF',
  'OUTSOURCE_COMPANY',
]

// 模块级单例 Promise：跨组件共享同一份加载中/已完成的数据。
let pendingLoad: Promise<LocationTreeNode[]> | null = null

/** 把 el-tree-select 的选中数组拆成「大类」与「具体 holder」两组。 */
export function splitLocationSelection(
  picked: string[],
): { locations: string[]; holderIds: string[] } {
  const locationSet = new Set<string>(PART_LOCATION_VALUES)
  const locations: string[] = []
  const holderIds: string[] = []
  for (const v of picked) {
    if (locationSet.has(v)) {
      if (!locations.includes(v)) locations.push(v)
    } else {
      holderIds.push(v)
    }
  }
  return { locations, holderIds }
}

export function usePartLocationTree(): {
  tree: Ref<LocationTreeNode[]>
  loading: Ref<boolean>
  load: () => Promise<void>
} {
  const tree = ref<LocationTreeNode[]>([])
  const loading = ref(false)

  async function doFetch(): Promise<LocationTreeNode[]> {
    const { items } = await getPartLocationTree()
    return items ?? []
  }

  /** 幂等加载：多次调用复用同一份 Promise。 */
  async function load(): Promise<void> {
    if (pendingLoad) {
      loading.value = true
      try {
        tree.value = await pendingLoad
      } finally {
        loading.value = false
      }
      return
    }
    loading.value = true
    pendingLoad = doFetch()
    try {
      tree.value = await pendingLoad
    } catch (e) {
      ElMessage.error((e as Error).message ?? '所在位置树加载失败')
      tree.value = []
      pendingLoad = null // 失败放行，下次重试
    } finally {
      loading.value = false
    }
  }

  return { tree, loading, load }
}