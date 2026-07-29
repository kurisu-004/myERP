<script setup lang="ts">
// PartPickerDialog — 送货单「添加零件」弹框
// 2026-07-23 增强：把「按序列号粘贴」改为「复选勾选」交互，
// 数据源 = GET /delivery-notes/candidate-parts?customer_id=<L1>
// 2026-07-29 批次化：行=批次；数量可编辑（改小后后端入单自动拆分）。
//
// 文档：
//   - el-dialog 用法：references/feedback.md §ElDialog
//   - el-table type="selection" + row-key + @selection-change：references/table.md §4
//   - el-input-number：references/form.md §InputNumber
//     > Source: https://element-plus.org/zh-CN/component/input-number.html

import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type {
  DeliveryNoteCandidatePart,
} from '@/types/deliveryNote'
import { listCandidateParts, type AddPartsItem } from '@/api/deliveryNote'

const props = defineProps<{
  /** v-model 兼容（标准命名 modelValue + update:modelValue 来自 el-dialog 习惯） */
  modelValue: boolean
  /** L1 一级客户雪花 ID 字符串（必填；为 '' 时不加载） */
  customerId: string
  /** 已在本单上的批次 id 列表 — 显示但置灰，避免重复选择 */
  existingBatchIds?: string[]
  /** 弹框标题 */
  title?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
  /** 用户点确认时回传勾选的批次条目（batch_id + 入单数量） */
  submit: [items: AddPartsItem[]]
}>()

const loading = ref(false)
const rows = ref<DeliveryNoteCandidatePart[]>([])
const selectedRows = ref<DeliveryNoteCandidatePart[]>([])
/** 每个勾选批次的入单数量（默认批次全量；可改小 → 后端自动拆分） */
const qtyMap = ref<Record<string, number>>({})

const existingSet = computed(
  () => new Set(props.existingBatchIds ?? []),
)

// 监听 customerId / 打开 → 拉候选
watch(
  () => [props.modelValue, props.customerId] as const,
  async ([open, cid]) => {
    if (!open || !cid) return
    loading.value = true
    try {
      rows.value = await listCandidateParts(cid)
      selectedRows.value = []
      qtyMap.value = {}
    } catch (e: unknown) {
      ElMessage.error((e as Error).message ?? '加载候选零件失败')
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

function rowSelectable(row: DeliveryNoteCandidatePart): boolean {
  return !existingSet.value.has(row.batch_id)
}

function onSelectionChange(rowsSel: DeliveryNoteCandidatePart[]) {
  selectedRows.value = rowsSel
  // 新勾选的行默认全量；取消勾选的行清掉数量
  const next: Record<string, number> = {}
  for (const r of rowsSel) {
    next[r.batch_id] = qtyMap.value[r.batch_id] ?? r.quantity
  }
  qtyMap.value = next
}

function qtyOf(row: DeliveryNoteCandidatePart): number {
  return qtyMap.value[row.batch_id] ?? row.quantity
}

function onSubmit() {
  const items: AddPartsItem[] = selectedRows.value.map((r) => ({
    batch_id: r.batch_id,
    quantity: qtyOf(r),
  }))
  emit('submit', items)
  emit('update:modelValue', false)
}

function onCancel() {
  emit('update:modelValue', false)
}

function statusTagType(s: string): 'warning' | 'success' | 'info' {
  if (s === 'READY_TO_SHIP') return 'success'
  if (s === 'INSPECTION') return 'warning'
  return 'info'
}
function statusLabel(s: string): string {
  if (s === 'READY_TO_SHIP') return '已通过品检'
  if (s === 'INSPECTION') return '待检'
  return s
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title ?? '选择零件'"
    width="980"
    :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:modelValue', v)"
  >
    <div class="picker-toolbar">
      <span class="picker-tip">
        行=批次；数量默认批次全量，改小后入单时自动拆分。已在本单上的批次不可勾选
      </span>
      <span class="picker-count">
        已勾 {{ selectedRows.length }} 批
      </span>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      row-key="batch_id"
      height="500"
      empty-text="该一级客户下暂无可入单的批次（INSPECTION / READY_TO_SHIP）"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="55" :selectable="rowSelectable" />
      <el-table-column label="批次" min-width="100" align="center">
        <template #default="{ row }">
          <span class="batch-label">{{ row.batch_label }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="serial_no" label="序列号" min-width="110" align="center"/>
      <el-table-column prop="drawing_no" label="图号" min-width="110" align="center"/>
      <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip align="center"/>
      <el-table-column label="批次量" width="80" align="right">
        <template #default="{ row }">{{ row.quantity }}</template>
      </el-table-column>
      <el-table-column label="入单数量" width="150" align="center">
        <template #default="{ row }">
          <el-input-number
            v-if="selectedRows.some((r) => r.batch_id === (row as DeliveryNoteCandidatePart).batch_id)"
            :model-value="qtyOf(row as DeliveryNoteCandidatePart)"
            :min="1"
            :max="(row as DeliveryNoteCandidatePart).quantity"
            :precision="0"
            size="small"
            style="width: 120px"
            @update:model-value="(v: number | undefined) => { const r = row as DeliveryNoteCandidatePart; qtyMap = { ...qtyMap, [r.batch_id]: v ?? r.quantity } }"
          />
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="applicant_name" label="申请人" min-width="90" align="center"/>
      <el-table-column label="状态" min-width="110" align="center">
        <template #default="{ row }">
          <el-tag
            :type="statusTagType(row.status)"
            effect="light"
            size="small"
          >
            {{ statusLabel(row.status) }}
          </el-tag>
          <el-tag
            v-if="existingSet.has(row.batch_id)"
            type="info"
            effect="plain"
            size="small"
            style="margin-left: 4px"
          >
            已在单上
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="planned_delivery_date" label="交期" min-width="110" align="center"/>
    </el-table>

    <template #footer>
      <el-button @click="onCancel">取消</el-button>
      <el-button
        type="primary"
        :disabled="!selectedRows.length"
        @click="onSubmit"
      >
        加入{{ selectedRows.length ? `（${selectedRows.length}）` : '' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.picker-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.picker-count {
  font-weight: 600;
  color: var(--el-color-primary);
}
.batch-label {
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace;
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>
