// composables/useRowEditor.ts
//
// 表格行内编辑状态机（2026-07-24 新增）。
// 抽象出 PartsList / AssemblyList 共用的「双击进入编辑 / 回车保存 / 取消」逻辑。
//
// 设计：
// - editingId 当前正在编辑的行 id（与列表 items 用同一 id 类型）。
// - editBuffer 是该行可编辑字段的暂存对象，startEdit 时深拷贝，saveEdit 时取出。
// - editingId 切换时自动挂/卸 document keydown 监听：回车键触发 saveEdit。
//   - 黑名单：.filter-card（搜索框区）、.el-popper（popper 下拉）—— 避免误触发。
// - 用 onBeforeUnmount 卸载监听器，避免内存泄漏。
//
// 用法：
//   const { editingId, editBuffer, startEdit, saveEdit, cancelEdit } = useRowEditor({
//     items: parts,
//     canEdit: computed(() => permissions.isManager.value || permissions.isClerk.value),
//     editableFields: ['drawing_no', 'name', 'quantity', 'unit_price', 'order_no', 'note'],
//     onSave: async (row, payload) => { await updatePart(row.id, payload) },
//   })

import { onBeforeUnmount, ref, watch, type ComputedRef, type Ref } from 'vue'

export interface UseRowEditorOptions<TRow extends { id: string }> {
  items: Ref<TRow[]>
  /** 当前账号是否有编辑权限（决定 onRowDblClick 是否进入编辑） */
  canEdit: ComputedRef<boolean> | Ref<boolean>
  /** 该类型行可编辑字段的白名单（其他字段不允许在 editBuffer 中变更） */
  editableFields: ReadonlyArray<keyof TRow>
  /** 保存回调：caller 实现具体 PUT/POST */
  onSave: (row: TRow, payload: Partial<TRow>) => Promise<void>
}

/**
 * 黑名单：在这些元素上按回车不会触发 save（如搜索框、popper 下拉）。
 * 这里用最常见的 el-* 选择器 + 自定义 .filter-card 类。
 */
const ENTER_BLACKLIST_SELECTORS = [
  '.filter-card',
  '.el-popper.is-light',
  '.el-select-dropdown',
  '.el-tree-select__popper',
  '.el-cascader__dropdown',
  '.el-date-picker',
]

function isInEnterBlacklist(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  for (const sel of ENTER_BLACKLIST_SELECTORS) {
    if (target.closest(sel)) return true
  }
  return false
}

export interface UseRowEditorApi<TRow extends { id: string }> {
  editingId: Ref<string | null>
  editBuffer: Ref<Partial<TRow>>
  isEditing: (row: TRow) => boolean
  startEdit: (row: TRow) => void
  saveEdit: (row: TRow) => Promise<void>
  cancelEdit: () => void
  onRowDblClick: (row: TRow) => void
}

export function useRowEditor<TRow extends { id: string }>(
  options: UseRowEditorOptions<TRow>,
): UseRowEditorApi<TRow> {
  const { items, canEdit, editableFields, onSave } = options
  const editingId = ref<string | null>(null)
  const editBuffer = ref<Partial<TRow>>({}) as Ref<Partial<TRow>>

  function isEditing(row: TRow): boolean {
    return editingId.value === row.id
  }

  function startEdit(row: TRow): void {
    if (!canEdit.value) return
    const buf: Partial<TRow> = {}
    for (const k of editableFields) {
      ;(buf as Record<string, unknown>)[k as string] = (row as Record<string, unknown>)[
        k as string
      ]
    }
    editBuffer.value = buf
    editingId.value = row.id
  }

  async function saveEdit(row: TRow): Promise<void> {
    if (editingId.value !== row.id) return
    try {
      await onSave(row, { ...editBuffer.value })
      editingId.value = null
      editBuffer.value = {}
    } catch (err) {
      // caller 已弹 ElMessage；保留 editingId 让用户继续修改
      console.error('[useRowEditor] saveEdit failed', err)
    }
  }

  function cancelEdit(): void {
    editingId.value = null
    editBuffer.value = {}
  }

  /**
   * 绑定到 `<ResponsiveList @row-dblclick="onRowDblClick">` 的处理器。
   * 双击未在编辑的行 → 进入编辑态；当前有别行在编辑 → 提示并拒绝（避免编辑冲突）。
   */
  function onRowDblClick(row: TRow): void {
    if (!canEdit.value) return
    if (editingId.value === row.id) return
    if (editingId.value) {
      // 用 setTimeout 让 ElMessage 不阻塞下一次 click
      setTimeout(() => {
        // 这里仅控制台告警；上层组件可自行 import ElMessage
        console.warn('[useRowEditor] 有未保存的编辑行，请先保存或取消')
      }, 0)
      return
    }
    startEdit(row)
  }

  function onEditEnter(e: KeyboardEvent): void {
    if (e.key !== 'Enter') return
    if (editingId.value == null) return
    if (isInEnterBlacklist(e.target)) return
    e.preventDefault()
    const row = items.value.find((r) => r.id === editingId.value)
    if (row) void saveEdit(row)
  }

  watch(editingId, (val) => {
    if (typeof document === 'undefined') return
    if (val != null) {
      document.addEventListener('keydown', onEditEnter)
    } else {
      document.removeEventListener('keydown', onEditEnter)
    }
  })

  onBeforeUnmount(() => {
    if (typeof document === 'undefined') return
    document.removeEventListener('keydown', onEditEnter)
  })

  return {
    editingId,
    editBuffer,
    isEditing,
    startEdit,
    saveEdit,
    cancelEdit,
    onRowDblClick,
  }
}