// composables/useListFilterPersist.ts
//
// 列表页筛选 / 排序 / 分页大小 / tab 等任意 state 的 localStorage 持久化。
// 由 PR-I（2026-07-20）原始 `useListFilterPersist` 在 PR-O（2026-07-30）泛化
// 为 `useListStatePersist`，可处理任意 shape（无 sort、双 tab、离散 ref）。
// 旧 `useListFilterPersist` 作为兼容包装保留，PartsList.vue 零改动。
//
// 用法（新泛型 API）：
//
//   const search = reactive<SearchState>({ keyword: '', statuses: [] })
//   const sortBy = ref<string>('PLANNED_DELIVERY_DATE')
//   const pageSize = ref(20)
//   const { restore, clear } = useListStatePersist(
//     'parts_list_filter',
//     { search, sortBy, pageSize },           // deps：Ref 或 reactive/plain 对象
//     { exclude: new Set(['page']) },         // 可选：跳过某些字段
//   )
//   onMounted(() => {
//     const s = restore()
//     if (s) {
//       Object.assign(search, s.search)
//       sortBy.value = s.sortBy as string
//       pageSize.value = s.pageSize as number
//     }
//     void fetchList()
//   })
//
// 设计要点：
// - 模块级 / 组件级都行；当前是组件级（每个列表页 mount 时新建实例）。
// - 每个 dep 单独 watch：Ref 看 .value；reactive/对象深 watch。
// - watch 节流 300ms，避免每次按键都写盘。
// - onBeforeUnmount 强制同步写一次（防页面关闭 timer 没触发）。
// - localStorage key 含 user.id 后缀，避免共享浏览器账号污染。
// - 不持久化 `page`（当前页码）—— 避免恢复时拉到不存在数据的页。
// - 用 vue 的 `isRef` 判断，绝不 duck-type `.value !== undefined`（reactive 对象
//   可能有 value 键，会误判）。

import { onBeforeUnmount, watch, isRef, type Ref } from 'vue'
import { useAuthSession } from './useAuthSession'

// ============ 共享 storageKey 工具 ============

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

// ============ 新：泛型 useListStatePersist ============

type Dep = Ref<unknown> | Record<string, unknown>

/** 泛型列表状态持久化。
 *
 * @param key 存储键（不含 userId 后缀，自动拼接）
 * @param deps 要持久化的状态字典。值可以是 Ref 或 reactive 对象（plain object 也可）。
 * @param options.exclude 不参与持久化的 key（如 page / offset）；这些字段写入时跳过，restore 时也不会写回
 * @param options.throttleMs watch 节流，默认 300
 *
 * 用法：
 *   const { restore, clear } = useListStatePersist('worker_list', { search })
 *   onMounted(() => {
 *     const s = restore()
 *     if (s) Object.assign(search, s.search)
 *   })
 */
export function useListStatePersist<T extends Record<string, Dep>>(
  key: string,
  deps: T,
  options: { exclude?: Set<string>; throttleMs?: number } = {},
) {
  const { exclude = new Set(), throttleMs = 300 } = options
  const KEY = storageKey(key)
  let timer: ReturnType<typeof setTimeout> | null = null

  /** 把 dep 读成可序列化值（Ref → .value；reactive → 自身浅拷）。 */
  function readDep(dep: Dep): unknown {
    if (isRef(dep)) return dep.value
    if (typeof dep === 'object' && dep !== null) return { ...(dep as Record<string, unknown>) }
    return dep
  }

  function snapshot(): void {
    try {
      const payload: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(deps)) {
        if (exclude.has(k)) continue
        payload[k] = readDep(v)
      }
      localStorage.setItem(KEY, JSON.stringify(payload))
    } catch {
      /* localStorage 满或被禁（隐私模式）静默失败 */
    }
  }

  function restore(): Record<string, unknown> | null {
    try {
      const raw = localStorage.getItem(KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw) as Record<string, unknown> | null
      if (!parsed || typeof parsed !== 'object') return null
      // 校验：每个 dep 的 key（排除 exclude 后）都必须存在于快照里
      for (const k of Object.keys(deps)) {
        if (exclude.has(k)) continue
        if (!(k in parsed)) return null
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

  // 每个 dep 单独 watch：Ref 看 .value；reactive/对象深 watch
  const stops: Array<() => void> = []
  for (const dep of Object.values(deps)) {
    const source = isRef(dep) ? () => dep.value : () => ({ ...(dep as Record<string, unknown>) })
    const stop = watch(source, () => {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(snapshot, throttleMs)
    }, { deep: true })
    stops.push(stop)
  }

  // 卸载前同步落盘（保险：watch timer 还没触发就关闭页面也能存住）
  onBeforeUnmount(() => {
    stops.forEach((stop) => stop())
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    snapshot()
  })

  return { restore, snapshot, clear }
}

// ============ 兼容：旧 useListFilterPersist API 包装 ============
//
// 原 shape { search, sortBy, sortDir, pageSize }；新泛型直接复用。
// PartsList 调用形态不变，restore() 仍返回带类型的 ListFilterPersistShape<T>。

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

export function useListFilterPersist<T extends object>(
  key: string,
  deps: ListFilterPersistDeps<T>,
) {
  // 新 API 直接接受 deps 对象；旧用户使用方式不变
  const { restore: rawRestore, snapshot, clear } = useListStatePersist(
    key,
    deps as unknown as Record<string, Dep>,
    { exclude: new Set<string>() },
  )
  // 保留旧 restore() 的类型签名，让 PartsList 的 `persisted.search.keyword` 等访问不丢类型
  function restore(): ListFilterPersistShape<T> | null {
    const r = rawRestore()
    if (!r) return null
    return r as unknown as ListFilterPersistShape<T>
  }
  return { restore, snapshot, clear }
}
