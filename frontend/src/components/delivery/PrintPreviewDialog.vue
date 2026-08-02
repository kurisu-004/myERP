<!--
  送货单打印预览对话框（2026-08-02 新增）。

  设计要点：
  - 列：序号（拖动 handle + 数字）/ 订单号 / 分厂 / 申请人 / 图号 / 名称 / 数量
  - 初始顺序 = 详情页当前 ``note.line_items`` 的内存顺序（含用户列头排序的结果）
  - 行可拖动：sortablejs 复用 ``PartBatchNew.vue`` 的低层 DOM API 模式
  - 用户拖动只影响预览副本；详情页 ``note.line_items`` 不变
  - 确认导出 → POST /delivery-notes/{id}/print body { custom_order }
  - 取消 → 关闭对话框
-->
<script setup lang="ts">
import {
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
const rows = ref<DeliveryNoteLineItem[]>([])
let sortable: Sortable | null = null
const loading = ref(false)

watch(
  () => props.modelValue,
  async (open) => {
    if (open && props.note) {
      // 拷贝当前内存顺序作为预览初始顺序（不污染详情页）
      rows.value = [...props.note.line_items]
      await nextTick()
      initSortable()
    } else {
      destroySortable()
    }
  },
)

watch(
  () => rows.value.length,
  () => nextTick(initSortable),
)

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
    // 把行 id 转字符串雪花 ID（detail.line_items[i].id 在 Pydantic IdStrNonNull 序列化为 str，
    // 但 TypeScript 端是 string）—— 与后端 API 契约一致
    const custom_order = rows.value.map((r) => String(r.id))
    const { blob, filename } = await printNote(
      props.note.id,
      { custom_order },
      (p: PrintNoteProgress) => {
        // 进度条可后续加；本轮先不动
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
      预览共 {{ rows.length }} 行；导出顺序 = 当前预览顺序
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
        prop="name" label="名称" min-width="180" show-overflow-tooltip align="center"/>
      <el-table-column
        prop="quantity" label="数量" min-width="80" align="right"/>
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
}
</style>