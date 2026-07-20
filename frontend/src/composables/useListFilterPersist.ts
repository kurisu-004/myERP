// composables/useListFilterPersist.ts
//
// 列表页筛选 / 排序 / 分页大小的 localStorage 持久化（PR-I 2026-07-20）。
// 用法（以 PartsList.vue 为例）：
//
//   const { restore, clear } = useListFilterPersist<SearchState>(
//     'parts_list_filter',
//     { search, sortBy, sortDir, pageSize },
//   )
//   onMounted(() => {
//     const s = restore()
//     if (s) {
//       Object.assign(search, s.search)
//       sortBy.value = s.sortBy
//       sortDir.value = s.sortDir
//       pageSize.value = s.pageSize
//     }
//     void fetchList()
//   })
//
// 设计要点：
// - 模块级 / 组件级都行；当前是组件级（每个列表页 mount 时新建实例）。
// - watch 节流 300ms，避免每次按键都写盘。
// - onBeforeUnmount 强制同步写一次（防页面关闭 timer 没触发）。
// - localStorage key 含 user.id 后缀，避免共享浏览器账号污染。
// - 不持久化 `page`（当前页码）—— 避免恢复时拉到不存在数据的页。

import { onBeforeUnmount, watch, type Ref } from 'vue'
import { useAuthSession } from './useAuthSession'

export interface ListFilterPersistShape<T> {
  search: T
  sortBy: string
  sortDir: string
  pageSize: number
}

export interface ListFilterPersistDeps<T extends object> {
  search: T
  sortBy: Ref<string>
  sortDir: Ref<string>
  pageSize: Ref<number>
}

/** 构造 localStorage key：含 user.id，避免多账号共享浏览器冲突。 */
function storageKey(key: string): string {
  let suffix = 'anon'
  try {
    const { user } = useAuthSession()
    if (user.value?.id) suffix = String(user.value.id)
  } catch {
    /* useAuthSession 在 setup 外调用会失败，落到 anon */
  }
  return `myerp.list.${suffix}.${key}`
}

export function useListFilterPersist<T extends object>(
  key: string,
  deps: ListFilterPersistDeps<T>,
) {
  const KEY = storageKey(key)
  let timer: ReturnType<typeof setTimeout> | null = null

  function snapshot(): void {
    try {
      const payload: ListFilterPersistShape<T> = {
        search: { ...deps.search } as T,
        sortBy: deps.sortBy.value,
        sortDir: deps.sortDir.value,
        pageSize: deps.pageSize.value,
      }
      localStorage.setItem(KEY, JSON.stringify(payload))
    } catch {
      /* localStorage 满或被禁（隐私模式）静默失败 */
    }
  }

  function restore(): ListFilterPersistShape<T> | null {
    try {
      const raw = localStorage.getItem(KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw) as ListFilterPersistShape<T>
      // 简单结构校验：缺字段就当作无快照
      if (
        !parsed
        || typeof parsed.sortBy !== 'string'
        || typeof parsed.sortDir !== 'string'
        || typeof parsed.pageSize !== 'number'
        || typeof parsed.search !== 'object'
        || parsed.search === null
      ) {
        return null
      }
      return parsed
    } catch {
      return null
    }
  }

  function clear(): void {
    try {
      localStorage.removeItem(KEY)
    } catch {
      /* 静默 */
    }
  }

  // watch 节流：300ms 内多次状态变化只写一次盘
  watch(
    [() => ({ ...deps.search }), deps.sortBy, deps.sortDir, deps.pageSize],
    () => {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(snapshot, 300)
    },
    { deep: true },
  )

  // 卸载前同步落盘（保险：watch timer 还没触发就关闭页面也能存住）
  onBeforeUnmount(() => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    snapshot()
  })

  return { restore, snapshot, clear }
}