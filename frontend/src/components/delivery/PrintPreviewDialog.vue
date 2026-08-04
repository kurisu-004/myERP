<!--
  送货单打印预览对话框（2026-08-02 新增；2026-08-04 装配件合并）。

  设计要点：
  - 列：序号（拖动 handle + 数字）/ 订单号 / 分厂 / 申请人 / 图号 / 名称 / 数量
  - 初始顺序 = 详情页当前 ``note.line_items`` 的内存顺序（含用户列头排序的结果）
  - 行可拖动：sortablejs 复用 ``PartBatchNew.vue`` 的低层 DOM API 模式
  - 用户拖动只影响预览副本；详情页 ``note.line_items`` 不变
  - 2026-08-04：单上有装配件子件时显示「合并为一套 / 分开打印所有子件」radio；
    合并模式预览折叠子件为父行；导出时把父行 round-trip 展开为组内 batch id 连续。
  - 确认导出 → POST /delivery-notes/{id}/print body { custom_order, merge_assemblies }
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
  type PrintNoteProgress,
} from '@/api/deliveryNote'
import { useDialogSize } from '@/composables/useDialogSize'
import { triggerBrowserDownload } from '@/utils/download'
import type {
  DeliveryNoteDetailOut,
  DeliveryNoteLineItem,
} from '@/types/deliveryNote'

const props = defineProps<{
  modelValue: boolean
  note: DeliveryNoteDetailOut | null
}>()

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
}>()

const dlg = useDialogSize({ desktopWidth: 1100, fullscreenOnMobile: true })

const previewTableRef = ref()
const rows = ref<PreviewRow[]>([])
let sortable: Sortable | null = null
const loading = ref(false)

// 2026-08-04：单上是否含有装配件子件
const hasAssemblies = computed(
  () => props.note?.line_items.some((li) => li.assembly_id) ?? false,
)
// 默认「分开打印所有子件」（安全默认；现状行为）
const mergeMode = ref<'separate' | 'merge'>('separate')

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

// 预览表格行：合并模式构造父行 + 散件；非合并模式 = line_items 拷贝
const previewRows = computed<PreviewRow[]>(() => {
  if (!props.note) return []
  const flat = props.note.line_items
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
      order_no: '',
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
    } else {
      destroySortable()
    }
  },
)

watch(previewRows, (next) => {
  rows.value = next
  nextTick(initSortable)
})

function isAsmRow(r: unknown): r is PreviewAssemblyRow {
  return typeof r === 'object' && r !== null
    && (r as PreviewAssemblyRow).is_asm_row === true
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
      const flat = props.note.line_items
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
    const { blob, filename } = await printNote(
      props.note.id,
      { custom_order, merge_assemblies: mergeFlag, merge_quantities },
      (p: PrintNoteProgress) => {
        void p
      },
    )
    triggerBrowserDownload(blob, filename)
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
    title="打印预览（拖动行可调整顺序）"
    :width="dlg.width.value"
    :top="dlg.top.value"
    :fullscreen="dlg.fullscreen.value"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div class="preview-tip">
      <span>预览共 {{ rows.length }} 行；导出顺序 = 当前预览顺序。</span>
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
    >
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
        type="primary" :loading="loading" :disabled="!rows.length" @click="onConfirm">
        导出 Excel
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