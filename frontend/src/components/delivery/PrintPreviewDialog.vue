<!--
  送货单打印预览对话框（2026-08-02 新增；2026-08-04 装配件合并；2026-08-07 拆双模式）。

  设计要点：
  - 列：勾选（仅 label 模式）/ 序号（拖动 handle + 数字）/ 订单号 / 分厂 / 申请人 / 图号 / 名称 / 数量
  - 初始顺序 = 详情页当前 ``note.line_items`` 的内存顺序（含用户列头排序的结果）
  - 行可拖动：sortablejs 复用 ``PartBatchNew.vue`` 的低层 DOM API 模式
  - 用户拖动只影响预览副本；详情页 ``note.line_items`` 不变
  - 2026-08-04：单上有装配件子件时显示「合并为一套 / 分开打印所有子件」radio；
    合并模式预览折叠子件为父行；导出时把父行 round-trip 展开为组内 batch id 连续。
  - 2026-08-07：新增 ``mode`` prop：
      · 'note'  = 只导送货单（不串联标签下载，旧 PR-C5 行为已剥离）
      · 'label' = 只导标签，支持勾选部分行；列首加 el-table 原生 selection 列
  - 取消 → 关闭对话框
-->
<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  ref,
  watch,
} from 'vue'
import { ElMessage } from 'element-plus'
import { Rank } from '@element-plus/icons-vue'
import Sortable from 'sortablejs'

import {
  printNote,
  printNoteLabels,
} from '@/api/deliveryNote'
import { useDialogSize } from '@/composables/useDialogSize'
import { triggerBrowserDownload } from '@/utils/download'
import type {
  DeliveryNoteDetailOut,
  DeliveryNoteLineItem,
} from '@/types/deliveryNote'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    note: DeliveryNoteDetailOut | null
    /** 2026-08-07：'note' = 只导送货单；'label' = 只导标签（可勾选行） */
    mode?: 'note' | 'label'
  }>(),
  { mode: 'note' },
)

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
}>()

const dlg = useDialogSize({ desktopWidth: 1100, fullscreenOnMobile: true })

const previewTableRef = ref()
const rows = ref<PreviewRow[]>([])
let sortable: Sortable | null = null
const loading = ref(false)

// 2026-08-07：标签模式的勾选状态
const selectedRows = ref<PreviewRow[]>([])

// 2026-08-04：单上是否含有装配件子件
const hasAssemblies = computed(
  () => props.note?.line_items.some((li) => li.assembly_id) ?? false,
)
// 2026-08-07 改默认：单上含装配件子件时直接合并为一套打印（与后端 merge_assemblies 默认一致）
const mergeMode = ref<'separate' | 'merge'>('merge')

// 2026-08-07：是否为标签导出模式
const isLabelMode = computed(() => props.mode === 'label')

interface PreviewAssemblyRow {
  id: string
  is_asm_row: true
  assembly_id: string
  order_no: string
  customer_name: string
  applicant_name: string
  drawing_no: string
  name: string
  quantity: number
  unit: string
}
type PreviewRow = DeliveryNoteLineItem | PreviewAssemblyRow

// 2026-08-07：同 part 多批次折叠（_split 产生同 part 同送货单）。
// 永远开启，先于装配体折叠：每 part 仅产出一行，quantity 求和；
// 行 id = 首个出现的 batch id（= 后端代表批次 id 约定）。
// 与 DeliveryNoteLineItem 1:1 → DeliveryNoteLineItem（保留原 part_id 用于 asm 折叠判断）。
function foldSamePart(items: DeliveryNoteLineItem[]): DeliveryNoteLineItem[] {
  const qty = new Map<string, number>()
  const rep = new Map<string, DeliveryNoteLineItem>()
  const order: string[] = []
  for (const li of items) {
    const pid = String(li.part_id)
    if (qty.has(pid)) {
      qty.set(pid, qty.get(pid)! + li.quantity)
      continue
    }
    qty.set(pid, li.quantity)
    rep.set(pid, li)
    order.push(pid)
  }
  return order.map((pid) => ({ ...rep.get(pid)!, quantity: qty.get(pid)! }))
}

// 预览表格行：合并模式构造父行 + 散件；非合并模式 = line_items 拷贝
const previewRows = computed<PreviewRow[]>(() => {
  if (!props.note) return []
  // 2026-08-07：先做同 part 折叠，再做装配体折叠。装配体折叠对折叠后的 part 唯一行生效。
  const flat = foldSamePart(props.note.line_items)
  if (!mergeMode.value || mergeMode.value === 'separate') {
    return [...flat]
  }
  const result: PreviewRow[] = []
  const insertedAsm = new Set<string>()
  flat.forEach((li) => {
    if (!li.assembly_id) {
      result.push(li)
      return
    }
    if (insertedAsm.has(li.assembly_id)) return
    const siblings = flat.filter((x) => x.assembly_id === li.assembly_id)
    result.push({
      id: `ASM_${li.assembly_id}`,
      is_asm_row: true,
      assembly_id: li.assembly_id,
      order_no: siblings[0]?.assembly_order_no ?? '',
      customer_name: siblings[0]?.customer_name ?? '',
      applicant_name: siblings[0]?.applicant_name ?? '',
      drawing_no: li.assembly_drawing_no ?? '',
      name: li.assembly_name ?? '',
      quantity: 1,
      unit: '套',
    })
    insertedAsm.add(li.assembly_id)
  })
  return result
})

watch(
  () => [props.modelValue, mergeMode.value],
  async ([open]) => {
    if (open && props.note) {
      // 拷贝当前内存顺序作为预览初始顺序（不污染详情页）
      rows.value = previewRows.value
      await nextTick()
      initSortable()
      // 2026-08-07：label 模式默认全选
      if (isLabelMode.value) {
        await selectAll()
      }
    } else {
      destroySortable()
    }
  },
)

watch(previewRows, async (next) => {
  rows.value = next
  await nextTick()
  initSortable()
  // 2026-08-07：合并模式切换后重置全选
  if (isLabelMode.value) {
    await selectAll()
  }
})

function isAsmRow(r: unknown): r is PreviewAssemblyRow {
  return typeof r === 'object' && r !== null
    && (r as PreviewAssemblyRow).is_asm_row === true
}

// 2026-08-07：标签模式全选 / 反选
async function selectAll(): Promise<void> {
  await nextTick()
  const t = previewTableRef.value
  if (!t) return
  // 逐行 toggle true（不用 toggleAllSelection：toggle 语义会全消）
  rows.value.forEach((r) => t.toggleRowSelection(r, true))
}
function invertSelection(): void {
  const t = previewTableRef.value
  if (!t) return
  const chosen = new Set(selectedRows.value)
  rows.value.forEach((r) => t.toggleRowSelection(r, !chosen.has(r)))
}

function initSortable(): void {
  const root = previewTableRef.value?.$el
  if (!root) return
  const tbody = root.querySelector(
    '.el-table__body-wrapper .el-table__body > tbody',
  ) as HTMLElement | null
  if (!tbody) return
  sortable?.destroy()
  sortable = Sortable.create(tbody, {
    handle: '.drag-handle',
    draggable: 'tr',
    animation: 150,
    ghostClass: 'sortable-ghost',
    onEnd(evt: { oldIndex?: number; newIndex?: number }) {
      const { oldIndex, newIndex } = evt
      if (oldIndex == null || newIndex == null || oldIndex === newIndex) return
      const next = rows.value.slice()
      const [moved] = next.splice(oldIndex, 1)
      if (moved) next.splice(newIndex, 0, moved)
      rows.value = next
    },
  })
}

function destroySortable(): void {
  sortable?.destroy()
  sortable = null
}

onBeforeUnmount(destroySortable)

function onCancel(): void {
  emit('update:modelValue', false)
}

async function onConfirm(): Promise<void> {
  if (!props.note) return
  loading.value = true
  try {
    let custom_order: string[]
    let mergeFlag = false
    let merge_quantities: Record<string, number> | undefined
    if (mergeMode.value === 'merge') {
      // 合并模式：父行 → 组内 batch id 连续；散件行原样
      custom_order = []
      merge_quantities = {}
      // 2026-08-07 bugfix：装配件子件被 _split 拆成多批时，未折叠的 line_items 会枚举到
      // 非代表 batch id，触发后端 custom_order rep-id 校验 422。改用 foldSamePart 后，
      // 每个 part 只产出一行（id = 代表 batch id），与后端 rep_by_part 一致。
      const flat = foldSamePart(props.note.line_items)
      rows.value.forEach((r) => {
        if (isAsmRow(r)) {
          merge_quantities![r.assembly_id] = r.quantity
          flat
            .filter((li) => li.assembly_id === r.assembly_id)
            .forEach((c) => custom_order.push(String(c.id)))
        } else {
          custom_order.push(String((r as DeliveryNoteLineItem).id))
        }
      })
      mergeFlag = true
    } else {
      custom_order = rows.value.map((r) => String((r as DeliveryNoteLineItem).id))
    }

    if (isLabelMode.value) {
      // 2026-08-07：label 模式 → 展开勾选行成子件 batch id（与 custom_order 同一口径）
      // 同上 bugfix：折叠后每个 asm 子件 part 仅 1 个代表 batch id。
      const flat = foldSamePart(props.note.line_items)
      const line_item_ids: string[] = []
      selectedRows.value.forEach((r) => {
        if (isAsmRow(r)) {
          flat
            .filter((li) => li.assembly_id === r.assembly_id)
            .forEach((c) => line_item_ids.push(String(c.id)))
        } else {
          line_item_ids.push(String((r as DeliveryNoteLineItem).id))
        }
      })
      const { blob, filename } = await printNoteLabels(props.note.id, {
        custom_order,
        merge_assemblies: mergeFlag,
        merge_quantities,
        line_item_ids,
      })
      triggerBrowserDownload(blob, filename)
    } else {
      // 2026-08-07：note 模式 → 仅导送货单，不再串联标签下载
      const { blob, filename } = await printNote(
        props.note.id,
        { custom_order, merge_assemblies: mergeFlag, merge_quantities },
      )
      triggerBrowserDownload(blob, filename)
    }
    ElMessage.success('已导出')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '导出失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="isLabelMode
      ? '标签打印预览（勾选要打印的行，拖动可调顺序）'
      : '打印预览（拖动行可调整顺序）'"
    :width="dlg.width.value"
    :top="dlg.top.value"
    :fullscreen="dlg.fullscreen.value"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div class="preview-tip">
      <span v-if="!isLabelMode">预览共 {{ rows.length }} 行；导出顺序 = 当前预览顺序。</span>
      <el-space v-if="isLabelMode" size="small">
        <span>已选 {{ selectedRows.length }} / {{ rows.length }} 行</span>
        <el-button size="small" @click="selectAll">全选</el-button>
        <el-button size="small" @click="invertSelection">反选</el-button>
      </el-space>
      <!-- 2026-08-04：仅当单上含装配件子件时显示（el-radio-button 更醒目） -->
      <el-radio-group
        v-if="hasAssemblies"
        v-model="mergeMode"
        size="small"
        class="merge-toggle"
      >
        <el-radio-button value="separate">分开打子件</el-radio-button>
        <el-radio-button value="merge">合并一套</el-radio-button>
      </el-radio-group>
    </div>
    <el-table
      ref="previewTableRef"
      :data="rows"
      row-key="id"
      stripe
      border
      height="500"
      @selection-change="(v: PreviewRow[]) => (selectedRows = v)"
    >
      <!-- 2026-08-07：label 模式首列加 el-table 原生 selection 勾选列 -->
      <el-table-column v-if="isLabelMode" type="selection" width="48" />
      <el-table-column width="72" align="center" label="序号">
        <template #default="{ $index }">
          <el-icon class="drag-handle" title="拖动排序"><Rank /></el-icon>
          <span class="row-index">{{ $index + 1 }}</span>
        </template>
      </el-table-column>
      <el-table-column
        prop="order_no" label="订单号" min-width="120" show-overflow-tooltip align="center">
        <template #default="{ row }">{{ row.order_no || '—' }}</template>
      </el-table-column>
      <el-table-column
        prop="customer_name" label="分厂" min-width="160" show-overflow-tooltip align="center">
        <template #default="{ row }">{{ row.customer_name || '—' }}</template>
      </el-table-column>
      <el-table-column
        prop="applicant_name" label="申请人" min-width="100" align="center">
        <template #default="{ row }">{{ row.applicant_name || '—' }}</template>
      </el-table-column>
      <el-table-column
        prop="drawing_no" label="图号" min-width="140" align="center"/>
      <el-table-column
        label="名称" min-width="180" show-overflow-tooltip align="center">
        <template #default="{ row }">
          <template v-if="isAsmRow(row)">
            <el-tag type="warning" size="small" class="asm-tag">装配件</el-tag>
            {{ row.name }}
          </template>
          <template v-else>{{ row.name }}</template>
        </template>
      </el-table-column>
      <el-table-column
        label="数量" min-width="120" align="right">
        <template #default="{ row }">
          <el-input-number
            v-if="isAsmRow(row)"
            v-model="row.quantity"
            :min="1" :max="999" :precision="0"
            size="small" controls-position="right"
            style="width: 110px"
          />
          <span v-else>{{ row.quantity }}</span>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="onCancel">取消</el-button>
      <el-button
        type="primary" :loading="loading"
        :disabled="!rows.length || (isLabelMode && !selectedRows.length)"
        @click="onConfirm">
        {{ isLabelMode ? '导出标签' : '导出送货单' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.drag-handle {
  cursor: grab;
  color: var(--primary-color);
  margin-right: 4px;
}
.drag-handle:active {
  cursor: grabbing;
}
.row-index {
  color: var(--text-secondary);
  font-size: 12px;
}
:deep(.sortable-ghost) {
  opacity: 0.4;
  background: #eaf2fb !important;
}
:deep(.sortable-chosen) {
  background: #cce0f4 !important;
}
.preview-tip {
  margin-bottom: 8px;
  color: var(--text-secondary);
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.merge-toggle {
  color: var(--text-primary);
}
.asm-tag { margin-right: 4px; }
</style>