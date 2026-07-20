<!--
  AssemblyList.vue

  /assemblies — 装配件列表，与 /parts 一览同款风格：
  - 顶部：搜索 + Reset + 共 N 条
  - 列头内嵌 popover：状态（多选 + 仅加急）、客户（cascader）
  - 序列号/图号/名称/计划交期 均可点表头排序
  - 加急行红底 #fde2e2
  - 无 # index 列；无删除按钮（移到详情页）

  操作列：详情（任意已登录）+ 取消（CLERK+，跳详情页点确认）
-->
<template>
  <div class="assembly-list">
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="search.keyword"
          placeholder="图号（含子串）"
          clearable
          style="width: 260px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-button @click="onReset">
          <el-icon><RefreshLeft /></el-icon>
          <span>重置</span>
        </el-button>

        <el-button type="primary" @click="$router.push('/assemblies/new')">
          <el-icon><Plus /></el-icon>
          <span>新建装配件</span>
        </el-button>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
      </div>
    </el-card>

    <div class="sheet-wrapper" v-loading="loading">
      <el-table
        :data="items"
        border
        stripe
        size="small"
        :default-sort="defaultSort"
        :row-class-name="rowClassName"
        @sort-change="onSortChange"
      >
        <el-table-column
          prop="serial_no"
          label="序列号"
          width="110"
          fixed="left"
          sortable="custom"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="drawing_no"
          label="总图图号"
          width="160"
          sortable="custom"
          show-overflow-tooltip
        />
        <el-table-column
          prop="name"
          label="名称"
          min-width="180"
          sortable="custom"
          show-overflow-tooltip
        />
        <el-table-column label="客户" min-width="200" show-overflow-tooltip>
          <template #header>
            <span class="header-cell">
              <span>客户</span>
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
                  <el-button
                    size="small"
                    type="primary"
                    @click="confirmCustomerFilter"
                  >确定</el-button>
                </div>
              </el-popover>
            </span>
          </template>
          <template #default="{ row }">
            {{ row.customer_path || '—' }}
          </template>
        </el-table-column>

        <el-table-column label="子零件" width="80" align="center">
          <template #default="{ row }">
            <el-tag type="info" size="small" effect="plain">
              {{ row.child_count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="planned_delivery_date"
          label="计划交期"
          width="120"
          sortable="custom"
        />

        <el-table-column
          label="状态"
          width="140"
          align="center"
        >
          <template #header>
            <span class="header-cell">
              <span>状态</span>
              <el-popover
                :width="200"
                placement="bottom-start"
                trigger="click"
                :show-arrow="false"
                v-model:visible="statusPopoverVisible"
                @show="syncStatusDraft"
              >
                <template #reference>
                  <el-icon
                    class="filter-icon"
                    :class="{ active: statusFilterActive }"
                  >
                    <Filter />
                  </el-icon>
                </template>
                <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                  多选状态 + 「仅加急」叠加加急过滤
                </div>
                <el-checkbox-group v-model="statusDraft">
                  <el-checkbox
                    v-for="opt in assemblyStatusOptions"
                    :key="opt.value"
                    :value="opt.value"
                    :label="opt.label"
                  />
                </el-checkbox-group>
                <el-checkbox
                  v-model="statusUrgentDraft"
                  label="仅加急"
                  style="margin-top: 8px; padding-top: 6px; border-top: 1px dashed var(--border-color-lighter)"
                />
                <div class="filter-actions">
                  <el-button size="small" link @click="resetStatusDraft">重置</el-button>
                  <el-button
                    size="small"
                    type="primary"
                    @click="confirmStatusFilter"
                  >确定</el-button>
                </div>
              </el-popover>
            </span>
          </template>
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              @click.stop="$router.push(`/assemblies/${row.id}`)"
            >
              详情
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="暂无符合条件的装配件" />
        </template>
      </el-table>
    </div>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        size="small"
        @current-change="fetchData"
        @size-change="onSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  ElMessage,
} from 'element-plus'
import { Filter, Plus, RefreshLeft, Search } from '@element-plus/icons-vue'
import { listAssemblies } from '@/api/assembly'
import {
  ASSEMBLY_STATUS_LABEL,
  ASSEMBLY_STATUS_TAG_TYPE,
  type AssemblyListItem,
  type AssemblyListQuery,
  type AssemblySortKey,
  type AssemblyStatus,
  type SortDir,
} from '@/types/assembly'
import { useCustomerTree } from '@/composables/useCustomerTree'

const { tree: customerTree } = useCustomerTree()
const route = useRoute()

// ============ 搜索条件 ============
interface SearchState {
  keyword: string
  statuses: string[]
  isUrgent: boolean | null
  customerId: string
}
function initialSearch(): SearchState {
  return { keyword: '', statuses: [], isUrgent: null, customerId: '' }
}
const search = reactive<SearchState>(initialSearch())

/** 状态列 → 中文 label（与 PartsList 同款）。 */
function statusLabel(s: string): string {
  return ASSEMBLY_STATUS_LABEL[s as AssemblyStatus] ?? s
}
function statusTagType(s: string): 'info' | 'warning' | 'success' | 'danger' | 'primary' {
  return ASSEMBLY_STATUS_TAG_TYPE[s as AssemblyStatus] ?? 'info'
}

const assemblyStatusOptions = (Object.keys(ASSEMBLY_STATUS_LABEL) as AssemblyStatus[])
  .map((v) => ({ value: v, label: ASSEMBLY_STATUS_LABEL[v] }))

const statusFilterActive = computed(
  () => search.statuses.length > 0 || search.isUrgent === true,
)
const customerFilterActive = computed(() => search.customerId !== '')

// ============ 状态列头 popover（draft + 确定/重置） ============
// 加急是独立的 boolean checkbox（不再用 marker 混入状态数组），彻底避免
// 不合法值污染传到后端的 statuses 字段。
const statusPopoverVisible = ref(false)
const statusDraft = ref<string[]>([])
const statusUrgentDraft = ref(false)

function syncStatusDraft(): void {
  statusDraft.value = [...search.statuses]
  statusUrgentDraft.value = search.isUrgent === true
}

function resetStatusDraft(): void {
  statusDraft.value = []
  statusUrgentDraft.value = false
  search.statuses = []
  search.isUrgent = null
  statusPopoverVisible.value = false
  onSearch()
}

function confirmStatusFilter(): void {
  search.statuses = [...statusDraft.value]
  search.isUrgent = statusUrgentDraft.value ? true : null
  statusPopoverVisible.value = false
  onSearch()
}

// ============ 客户列头 popover（draft + 确定/重置） ============
const customerPopoverVisible = ref(false)
const customerDraft = ref<string | null>(null)

function syncCustomerDraft(): void {
  customerDraft.value = search.customerId || null
}

function resetCustomerDraft(): void {
  customerDraft.value = null
  search.customerId = ''
  customerPopoverVisible.value = false
  onSearch()
}

function confirmCustomerFilter(): void {
  search.customerId = customerDraft.value ?? ''
  customerPopoverVisible.value = false
  onSearch()
}

// ============ 表格 / 排序 / 分页 ============
const items = ref<AssemblyListItem[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const sortBy = ref<AssemblySortKey>('PLANNED_DELIVERY_DATE')
const sortDir = ref<SortDir>('ASC')

const SORT_PROP_MAP: Record<string, AssemblySortKey> = {
  serial_no: 'SERIAL_NO',
  drawing_no: 'DRAWING_NO',
  name: 'NAME',
  planned_delivery_date: 'PLANNED_DELIVERY_DATE',
}
type SortOrder = 'ascending' | 'descending'
const defaultSort = computed<{ prop: string; order: SortOrder }>(() => ({
  prop: 'planned_delivery_date',
  order: sortDir.value === 'ASC' ? 'ascending' : 'descending',
}))

function rowClassName({ row }: { row: AssemblyListItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

async function fetchData(): Promise<void> {
  loading.value = true
  try {
    // 后端 schema: status 单值（不支持多选），所以多选时为空表示不过滤；
    // 仅加急只走 is_urgent 路径，不带 status。
    const base: AssemblyListQuery = {
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    }
    if (search.customerId) base.customer_id = search.customerId
    if (search.isUrgent !== null) base.is_urgent = search.isUrgent
    const trimmed = search.keyword.trim()
    if (trimmed) {
      // 仅按总图图号包含匹配；name_like 留空，避免 keyword 必须同时命中两列
      base.drawing_no_like = trimmed
    }
    // status 单值（多选展示但只透传第一个）
    if (search.statuses.length === 1) {
      base.status = search.statuses[0]
    }
    // statuses.length === 0 或 > 1：不传 status（不过滤）
    const res = await listAssemblies(base)
    items.value = res.items
    total.value = res.total
  } catch (e) {
    const msg = (e as Error).message || JSON.stringify(e)
    ElMessage.error(msg || '加载装配件列表失败')
    console.error('[AssemblyList] fetchData failed:', e)
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  page.value = 1
  void fetchData()
}

function onReset(): void {
  Object.assign(search, initialSearch())
  sortBy.value = 'PLANNED_DELIVERY_DATE'
  sortDir.value = 'ASC'
  page.value = 1
  void fetchData()
}

function onSizeChange(s: number): void {
  pageSize.value = s
  page.value = 1
  void fetchData()
}

function onSortChange({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void {
  if (!prop || !order) return
  sortBy.value = SORT_PROP_MAP[prop] ?? 'PLANNED_DELIVERY_DATE'
  sortDir.value = order === 'ascending' ? 'ASC' : 'DESC'
  void fetchData()
}

onMounted(() => {
  // 从 URL ?status=PENDING 等注入筛选（与新建后跳转保持一致）
  const q = route.query.status
  if (typeof q === 'string' && q in ASSEMBLY_STATUS_LABEL) {
    search.statuses = [q]
  }
  void fetchData()
})
</script>

<style lang="scss" scoped>
.assembly-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-card {
  :deep(.el-card__body) {
    padding: 12px 16px;
  }
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.total-hint {
  font-size: 13px;
  color: var(--text-secondary);
  margin-left: auto;
}

.sheet-wrapper {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 4px;
  overflow-x: auto;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  padding: 0 4px;
}

.muted {
  color: var(--text-secondary);
}

.header-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  justify-content: center;
}

.filter-icon {
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  &.active {
    color: var(--primary-color);
  }
}

.filter-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--border-color-lighter);
  padding-top: 8px;
}

:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}
</style>
