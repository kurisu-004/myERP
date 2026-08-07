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

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { TableInstance } from 'element-plus'
import type {
  DeliveryNoteCandidatePart,
} from '@/types/deliveryNote'
import { listCandidateParts, type AddPartsItem } from '@/api/deliveryNote'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import {
  findBySerialNo,
  findPartBySerialAndPrompt,
} from '@/utils/scanHelpers'
import type { PartItem } from '@/api/parts'
import BatchPickerDialog from '@/views/scan/components/BatchPickerDialog.vue'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
// 2026-08-07 picker 富化：L2 客户列 / 全屏 / 多选筛选 / 扫码拦截
//   - el-dialog fullscreen：references/feedback.md §ElDialog
//   - el-select multiple + collapse-tags + max-collapse-tags：form.md §el-select
//   - el-table-column sortable + prop：references/table.md §1
//   - ElMessage.warning：references/feedback.md §ElMessage

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

// 2026-08-04：扫码枪扫码勾选 — 仅按 serial_no 严格匹配（用户决定）。
// 候选行已 INSPECTION/READY_TO_SHIP 过滤，所以 0 命中 = 零件不在可入单状态 → 走报工台风格位置提示。
const tableRef = ref<TableInstance | null>(null)
/** 扫码命中行 0.8s 背景闪烁（row-class-name 用） */
const scanFlashBatchIds = ref<Set<string>>(new Set())
/** 扫码订阅句柄（弹框关闭时退订，避免 DeliveryNoteList 关闭 picker 后还在劫持扫码） */
const unsubPickerScan = ref<(() => void) | null>(null)
/** 同一 serial 在候选里多批次（极少见）— 复用报工台 BatchPickerDialog 选一个 */
const showPickerBatchPicker = ref(false)
const pickerBatchCode = ref('')
const pickerBatchRows = ref<PartItem[]>([])

const { onScan } = useBarcodeScanner()

const existingSet = computed(
  () => new Set(props.existingBatchIds ?? []),
)

// ============ 列可见性 ============
// 「selection 勾选列」「入单数量」操作列不放进 defs → 始终可见
const columnDefs = [
  { key: 'batch_label', label: '批次' },
  { key: 'serial_no', label: '序列号' },
  { key: 'drawing_no', label: '图号' },
  { key: 'name', label: '名称' },
  { key: 'order_no', label: '订单号' },
  { key: 'quantity', label: '批次量' },
  { key: 'customer_name', label: '二级客户' },  // 2026-08-07 picker 富化
  { key: 'applicant_name', label: '申请人' },
  { key: 'status', label: '状态' },
  { key: 'planned_delivery_date', label: '交期' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'delivery_part_picker' })

// ============ 2026-08-07：二级客户多选筛选 ============
/** 多选集合（每个元素是 customer_name 字符串）。空数组 = 不过滤。 */
const customerFilter = ref<string[]>([])

/** 工具栏下拉的选项：从已加载 rows 派生去重的 customer_name 列表。 */
const customerFilterOptions = computed(() => {
  const s = new Set<string>()
  for (const r of rows.value) {
    if (r.customer_name) s.add(r.customer_name)
  }
  return [...s].sort()
})

/** 筛选后的表格数据源（保留 selectedRows 在筛内外的合并逻辑见 onSelectionChange）。 */
const filteredRows = computed(() => {
  if (!customerFilter.value || customerFilter.value.length === 0) return rows.value
  const set = new Set(customerFilter.value)
  return rows.value.filter((r) => r.customer_name && set.has(r.customer_name))
})

/** 工具栏筛外已勾批次的数量（用于角标提示，避免用户被「勾了又看不到」困惑）。 */
const hiddenSelectedCount = computed(() => {
  if (!customerFilter.value || customerFilter.value.length === 0) return 0
  const set = new Set(customerFilter.value)
  return selectedRows.value.filter(
    (r) => !r.customer_name || !set.has(r.customer_name),
  ).length
})

// 监听 customerId / 打开 → 拉候选
watch(
  () => [props.modelValue, props.customerId] as const,
  async ([open, cid]) => {
    if (!open || !cid) {
      // 关闭时退订扫码，避免 DeliveryNoteList 上也被这个组件劫持
      unsubPickerScan.value?.()
      unsubPickerScan.value = null
      return
    }
    // 2026-08-07：重新打开弹框时清掉上一轮的 L2 筛选，避免陈旧状态误伤扫码。
    customerFilter.value = []
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
    // 打开后订阅扫码（只在第一次挂一次，避免 HMR 重复挂）
    if (!unsubPickerScan.value) {
      unsubPickerScan.value = onScan((code) => { void onPickerScan(code) })
    }
  },
  { immediate: true },
)

/** 2026-08-07 全屏表格高度：留出 toolbar + footer + dialog header 的空间。 */
const tableHeight = computed(() => 'calc(100vh - 240px)')

function rowSelectable(row: DeliveryNoteCandidatePart): boolean {
  return !existingSet.value.has(row.batch_id)
}

function onSelectionChange(rowsSel: DeliveryNoteCandidatePart[]) {
  // 2026-08-07：@selection-change 只反映当前可见行；保留之前被筛外勾上的批次，
  // 否则切筛选就会丢掉用户已选的勾（el-table 的 :data 切换会重置其内部 selection）。
  const visibleIds = new Set(rowsSel.map((r) => r.batch_id))
  const hiddenPrev = selectedRows.value.filter(
    (r) => !visibleIds.has(r.batch_id),
  )
  selectedRows.value = [...hiddenPrev, ...rowsSel]
  // 新勾选的行默认全量；取消勾选的行清掉数量
  const next: Record<string, number> = {}
  for (const r of selectedRows.value) {
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

// ============ 2026-08-04：扫码勾选 ============

/** 行闪烁 0.8s（row-class-name 用） */
function flashRow(batchId: string): void {
  scanFlashBatchIds.value = new Set([...scanFlashBatchIds.value, batchId])
  setTimeout(() => {
    const next = new Set(scanFlashBatchIds.value)
    next.delete(batchId)
    scanFlashBatchIds.value = next
  }, 800)
}

/** 程序化切换 el-table 选中状态，并同步本地 selectedRows / qtyMap。
 *  注意：toggleRowSelection 在「取消选中」分支不一定触发 @selection-change，需手动同步。 */
function toggleRowByBatchId(batchId: string): void {
  const table = tableRef.value
  if (!table) return
  const row = rows.value.find((r) => r.batch_id === batchId)
  if (!row) return
  const isSelected = selectedRows.value.some((r) => r.batch_id === batchId)
  if (isSelected) {
    table.toggleRowSelection(row, false)
    selectedRows.value = selectedRows.value.filter((r) => r.batch_id !== batchId)
    const nextQty = { ...qtyMap.value }
    delete nextQty[batchId]
    qtyMap.value = nextQty
  } else {
    table.toggleRowSelection(row, true)
    selectedRows.value = [...selectedRows.value, row]
    qtyMap.value = { ...qtyMap.value, [batchId]: row.quantity }
  }
}

/** 行 class — 用于扫码命中时 0.8s 背景闪烁 */
function rowClass({ row }: { row: DeliveryNoteCandidatePart }): string {
  return scanFlashBatchIds.value.has(row.batch_id) ? 'row-scan-flash' : ''
}

async function onPickerScan(rawCode: string): Promise<void> {
  const code = rawCode.trim()
  if (!code || !props.modelValue) return
  // 仅按 serial_no 严格匹配（用户决定 — barcodes = 工单 serial）
  const matches = findBySerialNo(
    rows.value as unknown as Array<{ serial_no: string | null }>,
    code,
  ) as unknown as DeliveryNoteCandidatePart[]
  // 过滤掉已在单上的批次（rowSelectable 已禁用，避免重复入单）
  const selectable = matches.filter(
    (r) => !existingSet.value.has(r.batch_id),
  )
  if (selectable.length === 0) {
    // 候选里没有 → 报工台风格位置提示（零件可能不在 INSPECTION/READY_TO_SHIP）
    await findPartBySerialAndPrompt(code)
    return
  }
  // 2026-08-07：二级客户筛选拦截 — 筛外的 serial 不勾选，ElMessage.warning 提示。
  // 仅在「全集中能命中 + 筛内 0 命中 + 用户确实设了筛选」时触发，避免无筛选时误报。
  const filterSet = new Set(customerFilter.value ?? [])
  if (filterSet.size > 0) {
    const inFilter = selectable.filter(
      (r) => r.customer_name != null && filterSet.has(r.customer_name),
    )
    if (inFilter.length === 0) {
      const actual = selectable[0]?.customer_name ?? '未知'
      ElMessage.warning(
        `扫取的图纸属于【${actual}】，不在筛选范围【${[...filterSet].join('、')}】内，未勾选`,
      )
      return
    }
    // 用筛内命中继续原流程
    return proceedSelect(inFilter)
  }
  return proceedSelect(selectable)

  function proceedSelect(list: DeliveryNoteCandidatePart[]) {
    if (list.length > 1) {
      // 同一 serial 多批次 — 复用报工台 BatchPickerDialog
      pickerBatchCode.value = code
      pickerBatchRows.value = list as unknown as PartItem[]
      showPickerBatchPicker.value = true
      return
    }
    // 单条命中 → 切换勾选 + 行闪烁
    const target = list[0]
    toggleRowByBatchId(target.batch_id)
    flashRow(target.batch_id)
  }
}

function onPickerBatchPicked(p: PartItem): void {
  showPickerBatchPicker.value = false
  const batchId = p.batch_id
  if (batchId) {
    toggleRowByBatchId(batchId)
    flashRow(batchId)
  }
}

onBeforeUnmount(() => {
  unsubPickerScan.value?.()
  unsubPickerScan.value = null
})
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title ?? '选择零件'"
    fullscreen
    :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:modelValue', v)"
  >
    <div class="picker-toolbar">
      <span class="picker-tip">
        行=批次；数量默认批次全量，改小后入单时自动拆分。已在本单上的批次不可勾选
      </span>
      <div class="picker-toolbar-right">
        <span class="picker-count">
          已勾 {{ selectedRows.length }} 批
          <span v-if="hiddenSelectedCount > 0" class="picker-count-hidden">
            （筛外 {{ hiddenSelectedCount }}）
          </span>
        </span>
        <!-- 2026-08-07：二级客户多选筛选 — 从已加载 rows 派生唯一 customer_name -->
        <el-select
          v-model="customerFilter"
          multiple
          filterable
          collapse-tags
          collapse-tags-tooltip
          :max-collapse-tags="2"
          clearable
          placeholder="筛选二级客户"
          style="width: 240px"
          class="picker-filter-select"
        >
          <el-option
            v-for="opt in customerFilterOptions"
            :key="opt"
            :label="opt"
            :value="opt"
          />
        </el-select>
        <ColumnVisibilityPopover
          :defs="columnDefs"
          :model-value="columnVisibility.currentMap"
          @update:model-value="columnVisibility.update"
          @reset="columnVisibility.showAll"
        />
      </div>
    </div>

    <el-table
      ref="tableRef"
      v-loading="loading"
      :data="filteredRows"
      row-key="batch_id"
      :height="tableHeight"
      empty-text="该一级客户下暂无可入单的批次（INSPECTION / READY_TO_SHIP）"
      :row-class-name="rowClass"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="55" :selectable="rowSelectable" />
      <el-table-column v-if="columnVisibility.isVisible('batch_label')" label="批次" min-width="100" align="center">
        <template #default="{ row }">
          <span class="batch-label">{{ row.batch_label }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="columnVisibility.isVisible('serial_no')" prop="serial_no" label="序列号" min-width="110" sortable align="center"/>
      <el-table-column v-if="columnVisibility.isVisible('drawing_no')" prop="drawing_no" label="图号" min-width="110" sortable align="center"/>
      <el-table-column v-if="columnVisibility.isVisible('name')" prop="name" label="名称" min-width="140" show-overflow-tooltip sortable align="center"/>
      <!-- 2026-08-01：图号后新增订单号列（与详情页一致），可排序 -->
      <el-table-column v-if="columnVisibility.isVisible('order_no')" prop="order_no" label="订单号" min-width="120" show-overflow-tooltip sortable align="center">
        <template #default="{ row }">
          <span :class="{ muted: !row.order_no }">{{ row.order_no || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="columnVisibility.isVisible('quantity')" label="批次量" width="80" align="right">
        <template #default="{ row }">{{ row.quantity }}</template>
      </el-table-column>
      <!-- 2026-08-07 picker 富化：二级客户列（prop 用于排序 / 筛选 / 显隐） -->
      <el-table-column
        v-if="columnVisibility.isVisible('customer_name')"
        prop="customer_name"
        label="二级客户"
        min-width="130"
        show-overflow-tooltip
        sortable
        align="center"
      >
        <template #default="{ row }">
          <span :class="{ muted: !row.customer_name }">{{ row.customer_name || '—' }}</span>
        </template>
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
      <el-table-column v-if="columnVisibility.isVisible('applicant_name')" prop="applicant_name" label="申请人" min-width="90" align="center"/>
      <el-table-column v-if="columnVisibility.isVisible('status')" label="状态" min-width="110" align="center">
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
      <el-table-column v-if="columnVisibility.isVisible('planned_delivery_date')" prop="planned_delivery_date" label="交期" min-width="110" sortable align="center"/>
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

  <!-- 2026-08-04：扫码命中同一 serial 多批次时复用报工台 BatchPickerDialog -->
  <BatchPickerDialog
    v-model="showPickerBatchPicker"
    :code="pickerBatchCode"
    :rows="pickerBatchRows"
    @pick="onPickerBatchPicked"
  />
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
.picker-toolbar-right {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}
.picker-count {
  font-weight: 600;
  color: var(--el-color-primary);
}
.picker-count-hidden {
  margin-left: 4px;
  font-weight: 400;
  color: var(--el-color-warning);
}
.picker-filter-select {
  margin-right: 12px;
}
.batch-label {
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace;
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
}

/* 2026-08-04：扫码命中行 0.8s 背景闪烁 */
@keyframes pickerScanFlash {
  0%   { background-color: #ecf5ff; }
  100% { background-color: transparent; }
}
:deep(.row-scan-flash td) {
  animation: pickerScanFlash 0.8s ease-out;
}
</style>
