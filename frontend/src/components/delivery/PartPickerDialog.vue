<script setup lang="ts">
// PartPickerDialog — 送货单「添加零件」弹框
// 2026-07-23 增强：把「按序列号粘贴」改为「复选勾选」交互，
// 数据源 = GET /delivery-notes/candidate-parts?customer_id=<L1>
//
// 文档：
//   - el-dialog 用法：references/feedback.md §ElDialog
//   - el-table type="selection" + row-key + @selection-change：references/table.md §4
//   - el-tag / el-date-picker：references/data-display.md / form.md

import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type {
  DeliveryNoteCandidatePart,
} from '@/types/deliveryNote'
import { listCandidateParts } from '@/api/deliveryNote'

const props = defineProps<{
  /** v-model 兼容（标准命名 modelValue + update:modelValue 来自 el-dialog 习惯） */
  modelValue: boolean
  /** L1 一级客户雪花 ID 字符串（必填；为 '' 时不加载） */
  customerId: string
  /** 已在本单上的 part_ids 列表 — 显示但置灰，避免重复选择 */
  existingPartIds?: string[]
  /** 弹框标题 */
  title?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
  /** 用户点确认时回传勾选出的 part id 列表 */
  submit: [partIds: string[]]
}>()

const loading = ref(false)
const rows = ref<DeliveryNoteCandidatePart[]>([])
const selectedRows = ref<DeliveryNoteCandidatePart[]>([])

const existingSet = computed(
  () => new Set(props.existingPartIds ?? []),
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
    } catch (e: unknown) {
      ElMessage.error((e as Error).message ?? '加载候选零件失败')
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

function rowSelectable(row: DeliveryNoteCandidatePart): boolean {
  return !existingSet.value.has(row.id)
}

function onSelectionChange(rowsSel: DeliveryNoteCandidatePart[]) {
  selectedRows.value = rowsSel
}

function onSubmit() {
  emit('submit', selectedRows.value.map((r) => r.id))
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
    width="900"
    :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:modelValue', v)"
  >
    <div class="picker-toolbar">
      <span class="picker-tip">
        勾选需要加入送货单的零件；已在本单上的件会显示但不可勾选
      </span>
      <span class="picker-count">
        已勾 {{ selectedRows.length }} 件
      </span>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      row-key="id"
      height="500"
      empty-text="该一级客户下暂无可入单的零件（INSPECTION / READY_TO_SHIP）"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="55" :selectable="rowSelectable" />
      <el-table-column prop="serial_no" label="序列号" min-width="140" />
      <el-table-column prop="drawing_no" label="图号" min-width="120" />
      <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
      <el-table-column prop="quantity" label="数量" width="80" align="right" />
      <el-table-column prop="applicant_name" label="申请人" min-width="100" />
      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tag
            :type="statusTagType(row.status)"
            effect="light"
            size="small"
          >
            {{ statusLabel(row.status) }}
          </el-tag>
          <el-tag
            v-if="existingSet.has(row.id)"
            type="info"
            effect="plain"
            size="small"
            style="margin-left: 4px"
          >
            已在单上
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="planned_delivery_date" label="交期" width="120" />
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
</style>
