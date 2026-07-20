<!--
  DeliveryNoteNew.vue — 文员生成送货单（2026-07-20 重设计）

  - 默认拉 status=READY_TO_SHIP 的零件列表（分页 + 可排序 + 列头筛选）
  - 关键字搜索：图号 / 名称前缀匹配（后端 repository/part.py _build_filter_stmt 已实现）
  - 客户筛选走列头 popover（用 useCustomerTree 的 el-tree-select，与 PartsList 同款）
  - 加急筛选：本视图走 inline 复选框（READY_TO_SHIP 状态下加急不是主要过滤维度，简化 UX）
  - el-table 多选 + 顶部「全选当前页 / 清空选择 / 已选 N 件」
  - 列：选择 | 流水号 | 图号 | 名称 | 订单号 | 系统交期 | 数量 | 分厂/客户 | 计划交期 | 备注
  - 加急行整行红底（与 PartsList 同款 row-urgent）
  - 顶部模板选择：自动 / 法拉 / 路达（el-radio-button 三段）
    「自动」= 后端按客户前缀分发；显式选法拉/路达时强制要求与零件所属 L1 root 一致
  - 「生成送货单」按钮 → 调 generateDeliveryNote(ids, template) 拿 Blob → 触发下载
-->
<template>
  <div class="delivery-note-new">
    <!-- 顶部模板选择 -->
    <el-card shadow="never" class="template-bar">
      <div class="template-row">
        <span class="template-label">送货单模板：</span>
        <el-radio-group v-model="templateChoice" size="small">
          <el-radio-button value="AUTO">自动</el-radio-button>
          <el-radio-button value="F">法拉</el-radio-button>
          <el-radio-button value="L">路达</el-radio-button>
        </el-radio-group>
        <span class="template-hint">
          模板选择仅影响生成 Excel，不影响列表查询
        </span>
      </div>
    </el-card>

    <!-- 关键字 + 加急 + 重置 + 总数 -->
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="search.keyword"
          placeholder="图号 / 名称（前缀搜索）"
          clearable
          style="width: 260px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-checkbox v-model="search.onlyUrgent" @change="onSearch">
          仅加急
        </el-checkbox>

        <el-button @click="onReset">
          <el-icon><RefreshLeft /></el-icon>
          <span>重置</span>
        </el-button>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条待送货</span>
      </div>
    </el-card>

    <div class="sheet-wrapper">
      <el-table
        :data="items"
        v-loading="loading"
        stripe
        border
        style="width: 100%"
        size="small"
        :default-sort="defaultSort"
        :row-class-name="rowClassName"
        row-key="id"
        :empty-text="emptyText"
        @sort-change="onSortChange"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="55" />

        <el-table-column
          prop="serial_no"
          label="流水号"
          width="110"
          show-overflow-tooltip
          sortable="custom"
        >
          <template #default="{ row }">
            <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="drawing_no"
          label="图号"
          width="130"
          show-overflow-tooltip
          sortable="custom"
        />

        <el-table-column
          prop="name"
          label="名称"
          min-width="200"
          show-overflow-tooltip
          sortable="custom"
        >
          <template #default="{ row }">
            <router-link :to="`/parts/${row.id}`" class="name-link">{{ row.name }}</router-link>
          </template>
        </el-table-column>

<el-table-column prop="applicant_name" label="申请人" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.applicant_name }">{{ row.applicant_name || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="order_no"
          label="订单号"
          width="130"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span :class="{ muted: !row.order_no }">{{ row.order_no || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="system_delivery_date"
          label="系统交期"
          width="110"
        >
          <template #default="{ row }">
            <span :class="{ muted: !row.system_delivery_date }">{{ row.system_delivery_date || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="quantity"
          label="数量"
          width="70"
          align="right"
        />

<el-table-column prop="unit_price" label="单价" width="90" align="right">
          <template #default="{ row }">{{ row.unit_price }}</template>
        </el-table-column>

        <!-- 客户列：列头 popover + el-tree-select -->
        <el-table-column label="分厂/客户" min-width="180" show-overflow-tooltip>
          <template #header>
            <span class="header-cell">
              <span>分厂/客户</span>
              <el-popover
                :width="280"
                placement="bottom-start"
                trigger="click"
                :show-arrow="false"
                v-model:visible="customerPopoverVisible"
                @show="syncCustomerDraft"
              >
                <template #reference>
                  <el-icon
                    class="filter-icon"
                    :class="{ active: customerFilterActive }"
                  >
                    <Filter />
                  </el-icon>
                </template>
                <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                  选一级客户自动级联其下二级客户
                </div>
                <el-tree-select
                  v-model="customerDraft"
                  :data="customerTree"
                  node-key="id"
                  :props="{ label: 'name', children: 'children' }"
                  check-strictly
                  clearable
                  filterable
                  placeholder="选择客户"
                  :teleported="false"
                  style="width: 100%"
                  @clear="customerDraft = null"
                />
                <div class="filter-actions">
                  <el-button size="small" link @click="resetCustomerDraft">重置</el-button>
                  <el-button size="small" type="primary" @click="confirmCustomerFilter">确定</el-button>
                </div>
              </el-popover>
            </span>
          </template>
          <template #default="{ row }">
            <span v-if="row.customer_path">{{ row.customer_path }}</span>
            <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

<el-table-column prop="request_date" label="请购日期" width="110">
          <template #default="{ row }">
            <span :class="{ muted: !row.request_date }">{{ row.request_date || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="planned_delivery_date"
          label="计划交期"
          width="120"
          show-overflow-tooltip
          sortable="custom"
        />

        <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.note }">{{ row.note || '—' }}</span>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100, 200]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="small"
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </div>
    </div>

    <div class="bottom-bar">
      <div class="bar-info">
        <span>已选 <strong>{{ selectedIds.length }}</strong> 件</span>
        <el-button link size="small" @click="onSelectAll">全选当前页</el-button>
        <el-button link size="small" @click="onClearSelection">清空选择</el-button>
        <span v-if="selectedIds.length > 0" class="customer-hint">
          · 客户：<strong>{{ inferredCustomerPath || '混合' }}</strong>
        </span>
      </div>
      <el-button
        type="primary"
        :loading="generating"
        :disabled="selectedIds.length === 0"
        @click="onGenerate"
      >
        <el-icon><Document /></el-icon>
        <span>生成送货单</span>
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Filter, RefreshLeft, Search } from '@element-plus/icons-vue'
import { listParts, type ListPartsParams } from '@/api/parts'
import {
  PART_SORT_PROP_MAP,
  type PartListItem,
  type PartSortKey,
  type SortDir,
} from '@/types/parts'
import { generateDeliveryNote, type DeliveryNoteTemplate } from '@/api/deliveryNote'
import { useCustomerTree } from '@/composables/useCustomerTree'

type TemplateChoice = 'AUTO' | 'F' | 'L'

const items = ref<PartListItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const generating = ref(false)

const page = ref(1)
const pageSize = ref(20)

const search = reactive<{ keyword: string; customerId: string; onlyUrgent: boolean }>({
  keyword: '',
  customerId: '',
  onlyUrgent: false,
})

const templateChoice = ref<TemplateChoice>('AUTO')
const { tree: customerTree } = useCustomerTree()

const selectedRows = ref<PartListItem[]>([])
const selectedIds = computed(() => selectedRows.value.map((r) => r.id))

// --- 排序 ---
const sortBy = ref<PartSortKey>('PLANNED_DELIVERY_DATE')
const sortDir = ref<SortDir>('ASC')

const defaultSort = computed<{ prop: string; order: 'ascending' | 'descending' }>(
  () => ({
    prop: 'planned_delivery_date',
    order: sortDir.value === 'ASC' ? 'ascending' : 'descending',
  }),
)

function onSortChange(args: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void {
  if (!args.prop || !args.order) return
  sortBy.value = PART_SORT_PROP_MAP[args.prop] ?? 'PLANNED_DELIVERY_DATE'
  sortDir.value = args.order === 'ascending' ? 'ASC' : 'DESC'
  page.value = 1
  void fetchList()
}

// --- 客户筛选（列头 popover：draft + 确定/重置 模式） ---
const customerPopoverVisible = ref(false)
const customerDraft = ref<string | null>(null)

const customerFilterActive = computed(() => Boolean(search.customerId))

function syncCustomerDraft(): void {
  customerDraft.value = search.customerId || null
}

function resetCustomerDraft(): void {
  customerDraft.value = null
  search.customerId = ''
  customerPopoverVisible.value = false
  page.value = 1
  void fetchList()
}

function confirmCustomerFilter(): void {
  search.customerId = customerDraft.value || ''
  customerPopoverVisible.value = false
  page.value = 1
  void fetchList()
}

const emptyText = computed(
  () => errorMsg.value ?? '暂无待送货零件（status=READY_TO_SHIP）',
)

/** 从选中行推断的客户路径（仅当全部同属一个 L1 root 时有值） */
const inferredCustomerPath = computed(() => {
  if (selectedRows.value.length === 0) return ''
  const paths = new Set(
    selectedRows.value
      .map((r) => r.customer_path ?? r.customer_name ?? '')
      .filter(Boolean),
  )
  if (paths.size === 1) return Array.from(paths)[0]
  return ''
})

function rowClassName({ row }: { row: PartListItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

function buildParams(): ListPartsParams {
  const params: ListPartsParams = {
    statuses: ['READY_TO_SHIP'],
    keyword: search.keyword.trim() || undefined,
    customer_id: search.customerId || undefined,
    is_urgent: search.onlyUrgent || undefined,
    sort_by: sortBy.value,
    sort_dir: sortDir.value,
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
  return params
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const resp = await listParts(buildParams())
    items.value = resp.items
    total.value = resp.total
    // 过滤掉已不在当前页的选中行（避免分页后带着「幽灵选中」生成）
    const validIds = new Set(items.value.map((r) => r.id))
    selectedRows.value = selectedRows.value.filter((r) => validIds.has(r.id))
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  page.value = 1
  void fetchList()
}

function onReset(): void {
  search.keyword = ''
  search.customerId = ''
  search.onlyUrgent = false
  customerDraft.value = null
  sortBy.value = 'PLANNED_DELIVERY_DATE'
  sortDir.value = 'ASC'
  page.value = 1
  void fetchList()
}

function onPageChange(): void {
  void fetchList()
}

function onPageSizeChange(): void {
  page.value = 1
  void fetchList()
}

function onSelectionChange(rows: PartListItem[]): void {
  selectedRows.value = rows
}

function onSelectAll(): void {
  selectedRows.value = [...items.value]
}

function onClearSelection(): void {
  selectedRows.value = []
}

async function onGenerate(): Promise<void> {
  if (selectedIds.value.length === 0) return
  const customer = inferredCustomerPath.value || '混合'
  const templateNote =
    templateChoice.value === 'AUTO'
      ? '（自动分发）'
      : `（${templateChoice.value === 'F' ? '法拉' : '路达'}）`
  try {
    await ElMessageBox.confirm(
      `将为客户「${customer}」生成送货单 Excel${templateNote}（${selectedIds.value.length} 件），确认继续？`,
      '生成送货单',
      { type: 'info', confirmButtonText: '确认生成', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  generating.value = true
  try {
    const template: DeliveryNoteTemplate | undefined =
      templateChoice.value === 'AUTO' ? undefined : templateChoice.value
    const blob = await generateDeliveryNote(selectedIds.value, template)
    _downloadBlob(blob, _todayFilename())
    ElMessage.success('送货单已生成，开始下载')
  } catch (e) {
    ElMessage.error(`生成失败：${(e as Error).message}`)
  } finally {
    generating.value = false
  }
}

function _todayFilename(): string {
  const d = new Date()
  const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `delivery_note_${ymd}.xlsx`
}

function _downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

onMounted(() => {
  // useCustomerTree composable 内部已 onMounted 自动 load 客户列表
  void fetchList()
})
</script>

<style scoped>
.delivery-note-new {
  padding: 0;
}
.template-bar {
  margin-bottom: 12px;
}
.template-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.template-label {
  color: var(--text-secondary);
  font-size: 13px;
}
.template-hint {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 12px;
}
.filter-card {
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.total-hint {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 13px;
}
.sheet-wrapper {
  background: #fff;
  border-radius: 6px;
  padding: 8px 0;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  padding: 12px 16px 0;
}
.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding: 12px 16px;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.bar-info {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 13px;
}
.bar-info strong {
  color: var(--el-color-primary);
  margin: 0 4px;
}
.customer-hint strong {
  color: var(--el-color-warning);
}
.name-link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.name-link:hover {
  text-decoration: underline;
}
.muted {
  color: var(--text-secondary);
}
/* 列头筛选图标（与 PartsList 同款） */
.header-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.filter-icon {
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
}
.filter-icon.active {
  color: var(--primary-color);
}
.filter-actions {
  margin-top: 8px;
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  border-top: 1px solid var(--border-color-lighter);
  padding-top: 6px;
}
/* 加急行红底（与 PartsList / Dashboard 同款） */
:deep(.row-urgent) {
  background-color: #fde2e2 !important;
}
:deep(.row-urgent:hover > td) {
  background-color: #fdd2d2 !important;
}
</style>