// composables/useActiveShelfSelection.ts
//
// 2026-07-13 新增：SHELF_ACCOUNT 多货架场景下，扫码台显式选择当前作业货架。
//
// 设计目标：
// - 单架 SHELF_ACCOUNT：自动选唯一架；UI 不显示选择器（与改造前行为一致）。
// - 多架 SHELF_ACCOUNT（≥ 2 架同/异 zone）：不自动选；ScanActionPicker 顶部
//   弹「当前货架」选择器；用户主动选一架；后续 PICK/RETURN/INSPECT 收口到
//   该架（单架时后端会校验，多架时若越权后端 403）。
// - wildcard SHELF_ACCOUNT（未绑任何 active 架）：不显示选择器（界面无目标）；
//   走通配逻辑（PICK 列表跨架、RETURN/INSPECT 后端返回 400 提示配置）。
// - 选择跨页持久化：sessionStorage（不是 localStorage）—— 跨账号切换
//   会自动失效；key 含 username 防账号互窜。
//
// 调用模式：
//   const sel = useActiveShelfSelection()
//   await sel.initShelves()
//   if (sel.showShelfSelector.value) { /* 渲染选择器 */ }
//   if (sel.selectedZone.value === 'PRODUCTION') { /* 显示 PICK_UP/RETURN */ }
//   const id = sel.selectedShelfId.value   // 提交 PICK/RETURN 时用

import { computed, ref } from 'vue'
import { listShelves } from '@/api/shelves'
import { useAuthSession } from '@/composables/useAuthSession'
import type { Shelf } from '@/types/shelf'

export interface ShelfOption {
  id: string
  code: string
  zone: 'PRODUCTION' | 'INSPECTION' | string
}

const SESSION_KEY_PREFIX = 'active_shelf_selection:'

export interface ActiveShelfSelection {
  /** 当前选中的货架 id（字符串）。null = 未选 / wildcard。 */
  selectedShelfId: import('vue').Ref<string | null>
  /** 用户能选的候选（绑定架详情；wildcard → 空数组）。 */
  options: import('vue').Ref<ShelfOption[]>
  /** 当前所选货架的 zone。 */
  selectedZone: import('vue').Ref<'PRODUCTION' | 'INSPECTION' | null>
  /** 是否处于「多架 + 必须显示选择器」状态。 */
  showShelfSelector: import('vue').ComputedRef<boolean>
  /** 加载候选架（进入扫码台时调一次）。 */
  initShelves: () => Promise<void>
  /** 显式清空选择（账号切换 / 重置用）。 */
  reset: () => void
}

export function useActiveShelfSelection(): ActiveShelfSelection {
  const { boundShelves, isWildcardShelfAccount, user } = useAuthSession()

  const selectedShelfId = ref<string | null>(null)
  const options = ref<ShelfOption[]>([])

  const sessionKey = computed(() =>
    user.value ? `${SESSION_KEY_PREFIX}${user.value.id}` : null,
  )

  function restoreFromSession(): string | null {
    const key = sessionKey.value
    if (!key || typeof window === 'undefined') return null
    try {
      return sessionStorage.getItem(key)
    } catch {
      return null
    }
  }

  function persistToSession(value: string | null): void {
    const key = sessionKey.value
    if (!key || typeof window === 'undefined') return
    try {
      if (value) sessionStorage.setItem(key, value)
      else sessionStorage.removeItem(key)
    } catch {
      // 忽略 storage 异常
    }
  }

  async function initShelves(): Promise<void> {
    const bound = boundShelves()
    if (bound.length === 0) {
      // wildcard：候选为空（界面不显示选择器，picker 走通配）
      options.value = []
      selectedShelfId.value = null
      persistToSession(null)
      return
    }

    // 拉所有 active 架（limit 200 足够车间用；超过说明架构问题）
    let shelves: Shelf[] = []
    try {
      shelves = (await listShelves({ is_active: true, limit: 200 })).items
    } catch {
      shelves = []
    }

    // 把 user.shelf_ids 转成详情列表（保持 user.shelf_ids 顺序）
    const boundSet = new Set(bound)
    const details: ShelfOption[] = []
    for (const sid of bound) {
      const s = shelves.find((x) => String(x.id) === String(sid))
      if (s) {
        details.push({ id: String(s.id), code: s.code, zone: s.zone })
      }
    }
    options.value = details
    // 兜底：万一后端 list_shelves 不全（不应发生），补一道
    if (details.length === 0) {
      for (const sid of bound) {
        details.push({ id: String(sid), code: `shelf#${sid}`, zone: 'PRODUCTION' })
      }
      options.value = details
    }

    // 决定 selectedShelfId
    if (details.length === 1) {
      // 单架：自动选
      selectedShelfId.value = details[0].id
      persistToSession(selectedShelfId.value)
    } else if (details.length >= 2) {
      // 多架：先看 sessionStorage 是否有之前的选择（且仍在 options 内）
      const stored = restoreFromSession()
      if (stored && boundSet.has(stored)) {
        selectedShelfId.value = stored
      } else {
        // 不自动选；让用户在 action picker 顶部选
        selectedShelfId.value = null
      }
    } else {
      selectedShelfId.value = null
    }
  }

  function reset(): void {
    selectedShelfId.value = null
    options.value = []
    persistToSession(null)
  }

  // 暴露 setSelected 让 ScanActionPicker 顶部选择器写回
  function setSelected(id: string | null): void {
    selectedShelfId.value = id
    persistToSession(id)
  }

  const selectedOption = computed<ShelfOption | null>(() => {
    const id = selectedShelfId.value
    if (!id) return null
    return options.value.find((o) => o.id === id) ?? null
  })

  const selectedZone = computed<'PRODUCTION' | 'INSPECTION' | null>(() => {
    const opt = selectedOption.value
    if (!opt) return null
    return opt.zone === 'PRODUCTION' || opt.zone === 'INSPECTION'
      ? opt.zone
      : null
  })

  // 多架（≥ 2 候选） + 当前没自动选 → 显示选择器
  const showShelfSelector = computed<boolean>(
    () => options.value.length >= 2 && selectedShelfId.value === null,
  )

  return {
    selectedShelfId: computed({
      get: () => selectedShelfId.value,
      set: (v) => setSelected(v),
    }) as ActiveShelfSelection['selectedShelfId'],
    options: computed(() => options.value) as ActiveShelfSelection['options'],
    selectedZone: computed(() => selectedZone.value) as ActiveShelfSelection['selectedZone'],
    showShelfSelector,
    initShelves,
    reset,
  }
}
