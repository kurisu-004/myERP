// composables/useColumnVisibility.ts
//
// 列可见性状态管理:每个 list 页面维护一个 `Record<columnKey, boolean>` 的 map,
// 通过 localStorage 跨会话持久化,key 约定沿用 `myerp.list.<userId>.<listKey>_columns`
// (与 useListStatePersist 保持一致 — 见 composables/useListFilterPersist.ts)。
//
// 与 useListStatePersist 的区别:
// - useListStatePersist 严格要求「快照包含所有 dep key」,新加列后旧快照会被丢弃;
//   列可见性场景下「加一列」是常见迭代,这种 strict 校验会导致历史保存的列状态被清空。
// - 这里采用 lenient 策略:从 localStorage 恢复时,只覆盖 defs 中已存在的 key,
//   新加的列自动用 defaultVisible(默认 true)。
//
// 用法:
//   const columnDefs = [
//     { key: 'serial', label: '序列号' },
//     { key: 'unit_price', label: '单价' },
//     { key: 'note', label: '备注', defaultVisible: false },  // 默认隐藏
//   ] as const
//   const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'parts_list' })
//
// 模板:
//   <el-table-column
//     v-if="columnVisibility.isVisible('serial')"
//     prop="serial_no" label="序列号" ...
//   />
//
// 列设置弹窗(见 components/ColumnVisibilityPopover.vue):
//   <ColumnVisibilityPopover :defs="columnDefs" v-model="columnVisibility.currentMap" />

import { computed, onBeforeUnmount, reactive, watch, type Ref, type WritableComputedRef } from 'vue'
import { useAuthSession } from './useAuthSession'

export interface ColumnDef {
  /** 唯一 key,英文/中文皆可,作为可见性 map 的 key */
  key: string
  /** 弹窗里 checkbox 的显示文字 */
  label: string
  /** 默认是否可见;缺省 true */
  defaultVisible?: boolean
}

export interface ColumnVisibilityApi {
  /** 当前可见性 map;key 不在 map 中视为 true。
   *  暴露为普通对象(`reactive`),而非 Ref/ComputedRef,这样 `v-model="columnVisibility.currentMap"`
   *  在 vue-tsc 下类型直通(原生 vue 的 v-model 模板展开,ref/computed 走特殊处理,
   *  vue-tsc 无法追踪)。 */
  currentMap: Record<string, boolean>
  /** 单 key 查询;未知 key 视为可见 */
  isVisible: (key: string) => boolean
  /** 切换单 key(value 缺省时取反) */
  toggle: (key: string, value?: boolean) => void
  /** 整表更新(从 el-checkbox-group 的「全选/全不选」或 popover 整表 emit 用)。
   *  原地突变 reactive Proxy,保持 watch 依赖不断。 */
  update: (next: Record<string, boolean>) => void
  /** 全部显示 */
  showAll: () => void
  /** 全部隐藏(操作列等不应隐藏的 key 不放进 defs 即可) */
  hideAll: () => void
  /** 所有声明的 key 列表(只读) */
  allKeys: readonly string[]
}

/** 构造 localStorage key:含 user.id 后缀,避免共享浏览器账号污染。 */
function storageKey(listKey: string): string {
  let suffix = 'anon'
  try {
    const { user } = useAuthSession()
    if (user.value?.id) suffix = String(user.value.id)
  } catch {
    /* useAuthSession 在 setup 外调用会失败 → 落到 anon */
  }
  return `myerp.list.${suffix}.${listKey}_columns`
}

/**
 * 初始化可见性 map(只包含 defs 中声明的 key)。
 * defs 中未声明的 key 一律视为可见,不需要进 map。
 */
function buildInitial(defs: readonly ColumnDef[]): Record<string, boolean> {
  const init: Record<string, boolean> = {}
  for (const d of defs) {
    init[d.key] = d.defaultVisible === false ? false : true
  }
  return init
}

/**
 * 从 localStorage 恢复:lenient 策略 — 只覆盖 defs 中已存在的 key;
 * 缺失的视为使用 defs 的默认(新加列的常见场景)。
 */
function restoreFromStorage(
  listKey: string,
  init: Record<string, boolean>,
): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(storageKey(listKey))
    if (!raw) return init
    const parsed = JSON.parse(raw) as Record<string, unknown> | null
    if (!parsed || typeof parsed !== 'object') return init
    const next: Record<string, boolean> = { ...init }
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === 'boolean' && k in init) {
        next[k] = v
      }
    }
    return next
  } catch {
    /* localStorage 被禁 / 解析失败 / 写入值非对象 → 静默回退到默认值 */
    return init
  }
}

function persistToStorage(listKey: string, value: Record<string, boolean>): void {
  try {
    localStorage.setItem(storageKey(listKey), JSON.stringify(value))
  } catch {
    /* 静默失败(localStorage 满、隐私模式等) */
  }
}

export function useColumnVisibility(
  defs: readonly ColumnDef[],
  options: { listKey: string },
): ColumnVisibilityApi {
  const allKeys = defs.map((d) => d.key)
  const initial = buildInitial(defs)
  // 用 reactive() 包装让 v-model 在 vue-tsc 下类型直通;
  // 写入/读取 currentMap[key] 自动触发响应式。
  const currentMap = reactive<Record<string, boolean>>(
    restoreFromStorage(options.listKey, initial),
  )

  // 防抖落盘(300ms),与 useListStatePersist 节奏一致
  let timer: ReturnType<typeof setTimeout> | null = null
  watch(
    currentMap,
    () => {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(() => persistToStorage(options.listKey, currentMap), 300)
    },
    { deep: true },
  )

  // 卸载前同步写一次(防止 timer 没触发就关闭页面)
  onBeforeUnmount(() => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    persistToStorage(options.listKey, currentMap)
  })

  function isVisible(key: string): boolean {
    return currentMap[key] !== false
  }

  function toggle(key: string, value?: boolean): void {
    const target = value === undefined ? !currentMap[key] : value
    // 原地修改,保持 Proxy 引用稳定(deep watch 仍会触发)
    currentMap[key] = target
  }

  /**
   * 整表更新(弹窗「全选/全不选/重置」或 `el-checkbox-group` 整表 emit 用):
   * 删掉旧 key 不在新表里的 + 写新表的 key,保持 reactive Proxy 引用稳定
   * (避免 `let currentMap = next` 替换引用,导致 watch / isVisible 失效)。
   */
  function update(next: Record<string, boolean>): void {
    for (const k of Object.keys(currentMap)) {
      if (!(k in next)) delete currentMap[k]
    }
    for (const [k, v] of Object.entries(next)) {
      currentMap[k] = v
    }
  }

  function showAll(): void {
    for (const k of allKeys) currentMap[k] = true
  }

  function hideAll(): void {
    for (const k of allKeys) currentMap[k] = false
  }

  return { currentMap, isVisible, toggle, update, showAll, hideAll, allKeys }
}
