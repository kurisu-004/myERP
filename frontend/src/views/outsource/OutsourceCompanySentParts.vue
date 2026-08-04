<!-- 外协对账一览（2026-07-28 新增；2026-07-29 基于 t_outsource_quote 重写）

基于 t_outsource_quote 统一事实表（外协全生命周期），列出所有送给该公司的零件：
- 图号 / 名称 / 客户（一级/二级）/ 数量 / 单价 / 总价（数量×单价）
- 发送时间 / 回收时间（未回收显示「未回收」）
- 状态（OUTSOURCING / RECEIVED / BILLED）/ 对账标记
- 排序：单价 / 发送时间 / 回收时间
- 双击行进入编辑（单价 + 数量 + 对账标记）；Enter 确认，Esc 取消（与零件一览一致）
- 表格底部：合计行（总价求和）+ 当前页总数
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type SummaryMethod } from 'element-plus'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  getOutsourceCompany,
  listCompanySentParts,
  reconcileUpdateShipment,
} from '@/api/outsource'
import type {
  OutsourceCompany,
  OutsourceReconciliationUpdatePayload,
  OutsourceSentPartItem,
  OutsourceSentPartSortKey,
} from '@/types/outsource'
import type { SortDir } from '@/types/parts'

const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()

const companyId = computed(() => String(route.params.id ?? ''))
const company = ref<OutsourceCompany | null>(null)
const items = ref<OutsourceSentPartItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const filter = reactive({
  keyword: '',
  sent_from: '' as string,  // ISO datetime-local string
  sent_to: '' as string,
})
const sortBy = ref<OutsourceSentPartSortKey>('SENT_AT')
const sortDir = ref<SortDir>('DESC')
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

// ============ 筛选状态持久化（2026-07-30 commit 4B）============
// 持久化 filter / sortBy / sortDir；该页无 page（接口固定 limit 50 offset 0）。
// companyId 走 URL，不进快照。
const { restore: restoreSentPartsFilter } = useListStatePersist(
  'outsource_company_sent_parts',
  { filter, sortBy, sortDir },
)

// ============ 列可见性 ============
const columnDefs = [
  { key: 'part_drawing_no', label: '图号' },
  { key: 'part_name', label: '名称' },
  { key: 'customer_path', label: '客户' },
  { key: 'batch_no', label: '批次号' },
  { key: 'quantity', label: '数量' },
  { key: 'unit_price', label: '单价' },
  { key: 'total_price', label: '总价' },
  { key: 'sent_at', label: '发送时间' },
  { key: 'received_at', label: '回收时间' },
  { key: 'status', label: '状态' },
  { key: 'is_billed', label: '对账' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'outsource_company_sent_parts' })

async function loadCompany(): Promise<void> {
  if (!companyId.value) return
  try {
    company.value = await getOutsourceCompany(companyId.value)
  } catch (e) {
    ElMessage.error(`外协公司加载失败：${(e as Error).message}`)
  }
}

async function loadList(): Promise<void> {
  if (!companyId.value) return
  loading.value = true
  error.value = null
  try {
    const r = await listCompanySentParts(companyId.value, {
      keyword: filter.keyword || undefined,
      sent_from: filter.sent_from || undefined,
      sent_to: filter.sent_to || undefined,
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
      limit: 50, offset: 0,
    })
    items.value = r.items
    total.value = r.total
  } catch (e) {
    items.value = []
    total.value = 0
    error.value = (e as Error).message ?? '加载对账列表失败'
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

function onReset(): void {
  filter.keyword = ''
  filter.sent_from = ''
  filter.sent_to = ''
  void loadList()
}

function onBack(): void {
  void router.push('/outsource/companies')
}

// ============ 排序（sortable="custom"）============
function onSortChange({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void {
  if (!prop || !order) return
  if (prop === 'unit_price') sortBy.value = 'PRICE'
  else if (prop === 'sent_at') sortBy.value = 'SENT_AT'
  else if (prop === 'received_at') sortBy.value = 'RECEIVED_AT'
  sortDir.value = order === 'ascending' ? 'ASC' : 'DESC'
  void loadList()
}

// ============ 行内编辑（2026-07-29，对齐 PartsList 双击编辑范式）============
interface EditBuffer {
  unit_price: number | null
  quantity: number | null
  is_billed: boolean
}
const editingId = ref<string | null>(null)  // shipment_id
const savingEdit = ref(false)
const editBuffer = reactive<EditBuffer>({
  unit_price: null,
  quantity: null,
  is_billed: false,
})

function startEdit(row: OutsourceSentPartItem): void {
  if (editingId.value && editingId.value !== row.shipment_id) {
    ElMessage.warning('请先保存或取消当前正在编辑的行')
    return
  }
  editBuffer.unit_price =
    row.unit_price !== null ? Number(row.unit_price) : null
  editBuffer.quantity = row.quantity
  editBuffer.is_billed = row.is_billed
  editingId.value = row.shipment_id
}

function onRowDblClick(row: OutsourceSentPartItem): void {
  startEdit(row)
}

// Enter 保存 / Esc 取消（黑名单：filter-card / 下拉 popper / 日期 picker）
const ENTER_BLACKLIST = [
  '.filter-card',
  '.el-popper.is-light',
  '.el-select-dropdown',
  '.el-date-picker',
]
function onEditEnter(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    if (editingId.value == null) return
    const target = e.target as HTMLElement | null
    if (target && ENTER_BLACKLIST.some((sel) => target.closest(sel))) return
    e.preventDefault()
    cancelEdit()
    return
  }
  if (e.key !== 'Enter') return
  if (editingId.value == null) return
  const target = e.target as HTMLElement | null
  if (target && ENTER_BLACKLIST.some((sel) => target.closest(sel))) return
  e.preventDefault()
  const row = items.value.find((r) => r.shipment_id === editingId.value)
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

function cancelEdit(): void {
  editingId.value = null
}

async function saveEdit(row: OutsourceSentPartItem): Promise<void> {
  if (editBuffer.quantity !== null && editBuffer.quantity < 1) {
    ElMessage.warning('数量必须 ≥ 1')
    return
  }
  if (editBuffer.unit_price !== null && editBuffer.unit_price < 0) {
    ElMessage.warning('单价必须 ≥ 0')
    return
  }
  savingEdit.value = true
  try {
    const payload: OutsourceReconciliationUpdatePayload = {
      version: row.version,
      unit_price: editBuffer.unit_price,
      quantity: editBuffer.quantity,
      is_billed: editBuffer.is_billed,
    }
    await reconcileUpdateShipment(row.shipment_id, payload)
    // 就地回填该行（避免整表刷新闪烁）；total_price 由 displayTotalPrice 实时算
    Object.assign(row, {
      unit_price:
        payload.unit_price !== null && payload.unit_price !== undefined
          ? String(payload.unit_price)
          : row.unit_price,
      quantity: payload.quantity ?? row.quantity,
      is_billed: payload.is_billed ?? row.is_billed,
      version: row.version + 1,
    })
    editingId.value = null
    ElMessage.success('保存成功')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    savingEdit.value = false
  }
}

// 总价列响应式显示（编辑态用 editBuffer 实时算，非编辑态用行数据）
function displayTotalPrice(row: OutsourceSentPartItem): string {
  if (editingId.value === row.shipment_id) {
    const q = Number(editBuffer.quantity ?? row.quantity ?? 0)
    const p = Number(editBuffer.unit_price ?? row.unit_price ?? 0)
    return Number.isFinite(q) && Number.isFinite(p) && q > 0
      ? (q * p).toFixed(2)
      : '—'
  }
  if (row.total_price !== null && row.total_price !== undefined) {
    return row.total_price
  }
  const q = Number(row.quantity ?? 0)
  const p = Number(row.unit_price ?? 0)
  return Number.isFinite(q) && Number.isFinite(p) && q > 0 && p > 0
    ? (q * p).toFixed(2)
    : '—'
}

// 状态列：OUTSOURCING / RECEIVED
const STATUS_LABEL: Record<string, string> = {
  OUTSOURCING: '外协中',
  RECEIVED: '已回收',
}
function statusLabel(s: string): string {
  return STATUS_LABEL[s] ?? s
}
function statusTagType(s: string): 'warning' | 'primary' | 'success' | 'info' {
  if (s === 'OUTSOURCING') return 'warning'
  if (s === 'RECEIVED') return 'primary'
  return 'info'
}

// 2026-08-04：加急行整行红底（与 PartsList / 看板同款）
function rowClassName({ row }: { row: OutsourceSentPartItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

// 表格底部合计行（总价列求和 + 第一列显示当前页总数）
const totalPriceSummary: SummaryMethod<OutsourceSentPartItem> = ({
  columns,
  data,
}) => {
  return columns.map((col, index) => {
    if (col.label === '总价') {
      const sum = data.reduce((acc, row) => {
        const q = Number(row.quantity ?? 0)
        const p = Number(row.unit_price ?? 0)
        return acc + (Number.isFinite(q) && Number.isFinite(p) ? q * p : 0)
      }, 0)
      return sum.toFixed(2)
    }
    if (index === 0) return `合计（本页 ${data.length} 条）`
    return ''
  })
}

function fmtDt(v: string | null): string {
  return v ? new Date(v).toLocaleString() : '—'
}

onMounted(() => {
  void loadCompany()
  // 2026-07-30 commit 4B：恢复 filter / sortBy / sortDir
  const persisted = restoreSentPartsFilter()
  if (persisted) {
    if (persisted.filter) Object.assign(filter, persisted.filter as Partial<typeof filter>)
    if (typeof persisted.sortBy === 'string') sortBy.value = persisted.sortBy as OutsourceSentPartSortKey
    if (typeof persisted.sortDir === 'string') sortDir.value = persisted.sortDir as SortDir
  }
  void loadList()
})

watch(companyId, () => {
  void loadCompany()
  void loadList()
})
</script>

<template>
  <div class="outsource-billing">
    <el-card shadow="never" class="filter-card">
      <div class="header-row">
        <h2 style="margin: 0; font-size: 16px;">
          外协对账：{{ company?.name ?? '...' }}
        </h2>
        <el-button size="small" @click="onBack">返回公司列表</el-button>
      </div>
      <el-form inline>
        <el-form-item label="关键字">
          <el-input v-model="filter.keyword" placeholder="图号 / 名称" clearable style="width: 160px" />
        </el-form-item>
        <el-form-item label="发送时间">
          <el-date-picker
            v-model="filter.sent_from"
            type="datetime"
            placeholder="起点"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 200px"
          />
          <span style="margin: 0 8px;">~</span>
          <el-date-picker
            v-model="filter.sent_to"
            type="datetime"
            placeholder="终点"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 200px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadList">查询</el-button>
          <el-button @click="onReset">重置</el-button>
          <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
        </el-form-item>
      </el-form>
    </el-card>

    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="shipment_id"
      :empty-text="error ?? '暂无对账记录'"
      :row-class-name="rowClassName"
      stripe
      border
      size="small"
      show-summary
      :summary-method="totalPriceSummary"
      @row-dblclick="onRowDblClick"
      @sort-change="onSortChange"
    >
      <template #toolbar>
        <ColumnVisibilityPopover
          :defs="columnDefs"
          :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
          @reset="columnVisibility.showAll"
        />
      </template>
      <el-table-column
        v-if="columnVisibility.isVisible('part_drawing_no')"
        prop="part_drawing_no" label="图号" min-width="120" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('part_name')"
        prop="part_name" label="名称" min-width="160" show-overflow-tooltip align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('customer_path')"
        prop="customer_path" label="客户" min-width="160" show-overflow-tooltip align="center"
      >
        <template #default="{ row }">
          {{ (row as OutsourceSentPartItem).customer_path ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('batch_no')"
        label="批次号" min-width="90" align="center"
      >
        <template #default="{ row }">
          {{ (row as OutsourceSentPartItem).batch_no ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('quantity')"
        label="数量" min-width="90" align="right"
      >
        <template #default="{ row }">
          <el-input-number
            v-if="editingId === (row as OutsourceSentPartItem).shipment_id"
            v-model="editBuffer.quantity"
            :min="1"
            :max="999999"
            :controls="false"
            size="small"
            style="width: 80px"
            :disabled="savingEdit"
          />
          <span v-else>{{ (row as OutsourceSentPartItem).quantity ?? '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('unit_price')"
        label="单价(元)" prop="unit_price" min-width="110" align="right" sortable="custom"
      >
        <template #default="{ row }">
          <el-input-number
            v-if="editingId === (row as OutsourceSentPartItem).shipment_id"
            v-model="editBuffer.unit_price"
            :min="0"
            :precision="2"
            :step="0.01"
            :controls="false"
            size="small"
            style="width: 100px"
            :disabled="savingEdit"
            placeholder="待填"
          />
          <span v-else>
            {{
              (row as OutsourceSentPartItem).unit_price !== null &&
              (row as OutsourceSentPartItem).unit_price !== undefined
                ? (row as OutsourceSentPartItem).unit_price
                : '—'
            }}
          </span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('total_price')"
        label="总价" min-width="110" align="right"
      >
        <template #default="{ row }">
          <span>{{ displayTotalPrice(row as OutsourceSentPartItem) }}</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('sent_at')"
        label="发送时间" prop="sent_at" min-width="160" align="center" sortable="custom"
      >
        <template #default="{ row }">
          {{ fmtDt((row as OutsourceSentPartItem).sent_at) }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('received_at')"
        label="回收时间" prop="received_at" min-width="160" align="center" sortable="custom"
      >
        <template #default="{ row }">
          <template v-if="(row as OutsourceSentPartItem).received_at">
            {{ fmtDt((row as OutsourceSentPartItem).received_at) }}
          </template>
          <span v-else style="color: var(--el-color-warning);">未回收</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('status')"
        label="状态" min-width="90" align="center"
      >
        <template #default="{ row }">
          <el-tag :type="statusTagType((row as OutsourceSentPartItem).status)" size="small">
            {{ statusLabel((row as OutsourceSentPartItem).status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('is_billed')"
        label="对账" min-width="80" align="center"
      >
        <template #default="{ row }">
          <el-switch
            v-if="editingId === (row as OutsourceSentPartItem).shipment_id"
            v-model="editBuffer.is_billed"
            size="small"
            :disabled="savingEdit"
          />
          <el-tag
            v-else-if="(row as OutsourceSentPartItem).is_billed"
            type="success"
            size="small"
          >已对</el-tag>
          <el-tag v-else type="info" size="small">未对</el-tag>
        </template>
      </el-table-column>

      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ (row as OutsourceSentPartItem).part_name || '未命名零件' }}</span>
          <el-tag :type="statusTagType((row as OutsourceSentPartItem).status)" size="small">
            {{ statusLabel((row as OutsourceSentPartItem).status) }}
          </el-tag>
        </div>
        <div class="rl-card-sub">
          图号 {{ (row as OutsourceSentPartItem).part_drawing_no || '—' }} ·
          客户 {{ (row as OutsourceSentPartItem).customer_path || '—' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">数量</span>
            <span class="rl-kv__val">{{ (row as OutsourceSentPartItem).quantity ?? '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">单价</span>
            <span class="rl-kv__val">{{ (row as OutsourceSentPartItem).unit_price ?? '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">总价</span>
            <span class="rl-kv__val">{{ displayTotalPrice(row as OutsourceSentPartItem) }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">发送时间</span>
            <span class="rl-kv__val">{{ fmtDt((row as OutsourceSentPartItem).sent_at) }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">回收时间</span>
            <span class="rl-kv__val">
              <template v-if="(row as OutsourceSentPartItem).received_at">
                {{ fmtDt((row as OutsourceSentPartItem).received_at) }}
              </template>
              <span v-else style="color: var(--el-color-warning);">未回收</span>
            </span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">对账</span>
            <span class="rl-kv__val">
              <el-tag
                v-if="(row as OutsourceSentPartItem).is_billed"
                type="success"
                size="small"
              >已对</el-tag>
              <el-tag v-else type="info" size="small">未对</el-tag>
            </span>
          </div>
        </div>
      </template>
    </ResponsiveList>
  </div>
</template>

<style lang="scss" scoped>
.outsource-billing {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.total-hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

// 2026-08-04：加急行整行红底（与 PartsList / 看板同款）
:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}
</style>
