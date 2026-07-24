<!--
  AssemblyList.vue

  /assemblies — 装配件列表，与 /parts 一览同款风格：
  - 顶部：搜索 + Reset + 共 N 条
  - 列头内嵌 popover：状态（多选 + 仅加急）、客户（cascader）
  - 序列号/图号/名称/计划交期 均可点表头排序
  - 加急行红底 #fde2e2
  - 无 # index 列；无删除按钮（移到详情页）

  操作列：详情（任意已登录）+ 取消（CLERK+，跳详情页点确认）

  PR-I 2026-07-20：INSPECTOR 看不到「新建装配件」按钮；筛选/排序/分页大小按账号持久化。
  PR-I 2026-07-21：手机/平板响应式（≤md 走卡片 + 抽屉筛选）。
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

        <!-- 手机筛选入口（桌面走表头 popover） -->
        <el-button v-if="isMobile" :type="anyFilterActive ? 'primary' : 'default'" plain @click="openMobileFilter">
          <el-icon><Filter /></el-icon>
          <span>筛选</span>
        </el-button>

        <el-button v-if="!isInspector" type="primary" @click="$router.push('/parts/new?tab=pdf')">
          <el-icon><Plus /></el-icon>
          <span>新建零件 / 装配件</span>
        </el-button>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
      </div>
    </el-card>

    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="id"
      :empty-text="emptyText"
      :card-class="(row) => (row.is_urgent ? 'rl-card--urgent' : '')"
      border
      stripe
      size="small"
      :default-sort="defaultSort"
      :row-class-name="rowClassName"
      show-summary
      :summary-method="totalPriceSummary"
      @sort-change="onSortChange"
      @row-dblclick="startEditAsm"
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

      <!-- 2026-07-24 新增：订单号列（与 PartsList 对齐） -->
      <el-table-column
        prop="order_no"
        label="订单号"
        width="130"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <el-input
            v-if="isEditing(row)"
            v-model="editBuffer.order_no"
            size="small"
          />
          <span v-else>{{ row.order_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="drawing_no"
        label="总图图号"
        width="160"
        sortable="custom"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <el-input
            v-if="isEditing(row)"
            v-model="editBuffer.drawing_no"
            size="small"
          />
          <span v-else>{{ row.drawing_no }}</span>
        </template>
      </el-table-column>
      <el-table-column
        prop="name"
        label="名称"
        min-width="180"
        sortable="custom"
        show-overflow-tooltip
      />
      <el-table-column label="申请人" width="110" show-overflow-tooltip>
        <template #default="{ row }">
          <span :class="{ muted: !row.applicant_name }">{{ row.applicant_name || '—' }}</span>
        </template>
      </el-table-column>
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

      <!-- 2026-07-24 新增：数量 / 单价 / 总价（与 PartsList 对齐） -->
      <el-table-column label="数量" width="90" align="right">
        <template #default="{ row }">
          <el-input-number
            v-if="isEditing(row)"
            v-model="editBuffer.quantity"
            :min="1"
            :precision="0"
            :controls="false"
            size="small"
            style="width: 80px"
          />
          <span v-else>{{ row.quantity ?? '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="单价" width="110" align="right">
        <template #default="{ row }">
          <el-input-number
            v-if="isEditing(row)"
            v-model="editBuffer.unit_price"
            :min="0"
            :precision="2"
            :step="0.01"
            :controls="false"
            size="small"
            style="width: 100px"
          />
          <span v-else>{{ row.unit_price ?? '—' }}</span>
        </template>
      </el-table-column>
      <!-- 2026-07-24 v2 调整：总价由 quantity × unit_price 前端实时计算（只读展示，与 PartsList 对齐） -->
      <el-table-column label="总价" width="120" align="right">
        <template #default="{ row }">
          <span v-if="isEditing(row)">
            {{ ((Number(editBuffer.quantity) || 0) * (Number(editBuffer.unit_price) || 0)).toFixed(2) }}
          </span>
          <span v-else>
            {{ ((Number(row.quantity) || 0) * (Number(row.unit_price) || 0)).toFixed(2) }}
          </span>
        </template>
      </el-table-column>

      <el-table-column label="子零件" width="80" align="center">
        <template #default="{ row }">
          <el-tag type="info" size="small" effect="plain">
            {{ row.child_count }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="请购日期" width="120">
        <template #default="{ row }">{{ row.request_date || '—' }}</template>
      </el-table-column>
      <el-table-column
        prop="planned_delivery_date"
        label="计划交期"
        width="120"
        sortable="custom"
      />

      <!-- 2026-07-24 新增：系统交期（与 PartsList 对齐） -->
      <el-table-column
        prop="system_delivery_date"
        label="系统交期"
        width="130"
      >
        <template #default="{ row }">
          <el-date-picker
            v-if="isEditing(row)"
            v-model="editBuffer.system_delivery_date"
            type="date"
            value-format="YYYY-MM-DD"
            size="small"
            style="width: 120px"
            clearable
          />
          <span v-else>{{ row.system_delivery_date || '—' }}</span>
        </template>
      </el-table-column>

      <!-- 2026-07-24 新增：备注（与 PartsList 对齐） -->
      <el-table-column label="备注" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <el-input
            v-if="isEditing(row)"
            v-model="editBuffer.note"
            size="small"
          />
          <span v-else>{{ row.note || '—' }}</span>
        </template>
      </el-table-column>

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

      <el-table-column label="操作" width="200" align="center" fixed="right">
        <template #default="{ row }">
          <template v-if="isEditing(row)">
            <el-button
              link
              type="primary"
              size="small"
              :loading="savingEdit"
              @click.stop="saveEditAsm(row)"
            >保存</el-button>
            <el-button
              link
              size="small"
              @click.stop="editingId = null"
            >取消</el-button>
          </template>
          <template v-else>
            <el-button
              link
              type="primary"
              size="small"
              @click.stop="$router.push(`/assemblies/${row.id}`)"
            >
              详情
            </el-button>
            <el-button
              v-if="canEditAsm"
              link
              type="warning"
              size="small"
              @click.stop="startEditAsm(row)"
            >
              编辑
            </el-button>
          </template>
        </template>
      </el-table-column>

      <!-- 手机卡片 -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ row.name }}</span>
          <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </div>
        <div class="rl-card-sub">
          总图图号 {{ row.drawing_no || '—' }} · 序列号 {{ row.serial_no || '—' }} ·
          订单号 {{ row.order_no || '—' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">客户</span>
            <span class="rl-kv__val">{{ row.customer_path || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">申请人</span>
            <span class="rl-kv__val">{{ row.applicant_name || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">数量</span>
            <span class="rl-kv__val">{{ row.quantity ?? '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">单价</span>
            <span class="rl-kv__val">{{ row.unit_price ?? '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">总价</span>
            <span class="rl-kv__val">{{ ((Number(row.quantity) || 0) * (Number(row.unit_price) || 0)).toFixed(2) }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">子零件</span>
            <span class="rl-kv__val">{{ row.child_count }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">请购日期</span>
            <span class="rl-kv__val">{{ row.request_date || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">计划交期</span>
            <span class="rl-kv__val">{{ row.planned_delivery_date || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">系统交期</span>
            <span class="rl-kv__val">{{ row.system_delivery_date || '—' }}</span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button
            link
            type="primary"
            size="small"
            @click.stop="$router.push(`/assemblies/${row.id}`)"
          >
            详情
          </el-button>
        </div>
      </template>
    </ResponsiveList>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        :layout="paginationLayout"
        :pager-count="isMobile ? 5 : 7"
        background
        size="small"
        @current-change="fetchData"
        @size-change="onSizeChange"
      />
    </div>

    <!-- 手机筛选抽屉：承载桌面表头 popover 的同款筛选（客户 + 状态 + 加急） -->
    <el-drawer
      v-model="mobileFilterOpen"
      title="筛选"
      direction="btt"
      size="72%"
    >
      <div class="mobile-filter">
        <div class="mf-section">
          <div class="mf-label">客户</div>
          <el-tree-select
            v-model="customerDraft"
            :data="customerTree"
            node-key="id"
            :props="{ label: 'name', children: 'children' }"
            check-strictly
            clearable
            filterable
            placeholder="选择客户"
            style="width: 100%"
            @clear="customerDraft = null"
          />
        </div>
        <div class="mf-section">
          <div class="mf-label">状态</div>
          <el-checkbox-group v-model="statusDraft" class="mf-status">
            <el-checkbox
              v-for="opt in assemblyStatusOptions"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-checkbox-group>
          <el-checkbox v-model="statusUrgentDraft" label="仅加急" class="mf-urgent" />
        </div>
      </div>
      <template #footer>
        <el-button @click="resetMobileFilter">重置</el-button>
        <el-button type="primary" @click="confirmMobileFilter">确定</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  ElMessage,
} from 'element-plus'
import type { SummaryMethodProps } from 'element-plus'
import { Filter, Plus, RefreshLeft, Search } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { listAssemblies, updateAssembly } from '@/api/assembly'
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
import { usePermissions } from '@/composables/usePermissions'
import { useListFilterPersist } from '@/composables/useListFilterPersist'

const { tree: customerTree } = useCustomerTree()
// PR-I 2026-07-20：INSPECTOR 看不到「新建装配件」按钮
const { isInspector, isManager, isClerk } = usePermissions()
const route = useRoute()
const { isMobile } = useBreakpoint()

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

// ============ 手机筛选抽屉 ============
const mobileFilterOpen = ref(false)
const anyFilterActive = computed(() => statusFilterActive.value || customerFilterActive.value)

function openMobileFilter(): void {
  syncStatusDraft()
  syncCustomerDraft()
  mobileFilterOpen.value = true
}
function confirmMobileFilter(): void {
  search.statuses = [...statusDraft.value]
  search.isUrgent = statusUrgentDraft.value ? true : null
  search.customerId = customerDraft.value ?? ''
  mobileFilterOpen.value = false
  onSearch()
}
function resetMobileFilter(): void {
  statusDraft.value = []
  statusUrgentDraft.value = false
  customerDraft.value = null
  search.statuses = []
  search.isUrgent = null
  search.customerId = ''
  mobileFilterOpen.value = false
  onSearch()
}

// 手机上分页收窄为 prev/pager/next，桌面保留完整布局
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)
const emptyText = '暂无符合条件的装配件'

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
  clearAsmFilter()
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

// ============ 行内编辑（2026-07-24）============
// MANAGER / CLERK 可双击编辑（与后端 POST /assemblies/{id}/update 权限一致）。
// 可编辑字段：drawing_no / quantity / unit_price / order_no /
//   system_delivery_date / note。
// 2026-07-24 v2：总价列从"独立可编辑"改为"前端实时计算 = quantity × unit_price"，
// 与 PartsList 对齐；不暴露给用户单独输入；后端 service/assembly.py 在
// quantity / unit_price / total_price 任一变更时自动重算 total_price。
// 注意：装配体本身 total_price > 0 时，service 层会主动清零子件价（见
// service/assembly.py::_clear_children_prices），并通过 dashboard 广播。
const ASM_EDITABLE_FIELDS = [
  'drawing_no',
  'quantity',
  'unit_price',
  'order_no',
  'system_delivery_date',
  'note',
] as const
interface AsmEditBuffer {
  drawing_no: string
  quantity: number
  unit_price: number
  order_no: string | null
  system_delivery_date: string | null
  note: string | null
}
const editBuffer = reactive<AsmEditBuffer>({
  drawing_no: '',
  quantity: 1,
  unit_price: 0,
  order_no: null,
  system_delivery_date: null,
  note: null,
})
const canEditAsm = computed(() => isManager.value || isClerk.value)
const savingEdit = ref(false)

// 行内编辑状态（与 PartsList 同款本地实现，不依赖 useRowEditor）
const editingId = ref<string | null>(null)
function isEditing(row: AssemblyListItem): boolean { return editingId.value === row.id }

// 行内编辑入口：双击行进入编辑态
function startEditAsm(row: AssemblyListItem): void {
  if (!canEditAsm.value) return
  if (editingId.value && editingId.value !== row.id) {
    ElMessage.warning('请先保存或取消当前正在编辑的行')
    return
  }
  editBuffer.drawing_no = row.drawing_no
  editBuffer.quantity = row.quantity
  editBuffer.unit_price = row.unit_price
  editBuffer.order_no = row.order_no
  editBuffer.system_delivery_date = row.system_delivery_date
  editBuffer.note = row.note
  editingId.value = row.id
}

async function saveEditAsm(row: AssemblyListItem): Promise<void> {
  if (editingId.value !== row.id) return
  savingEdit.value = true
  try {
    // 2026-07-24 v2：总价由后端自动按 unit_price * quantity 重算，不在 payload 里显式传
    const res = await updateAssembly(row.id, { ...editBuffer })
    Object.assign(row, { ...editBuffer, total_price: res.total_price, version: res.version })
    ElMessage.success('保存成功')
    editingId.value = null
  } catch (e) {
    // 40901 = BIZ_VERSION_CONFLICT（乐观锁冲突）
    if ((e as { code?: number }).code === 40901) {
      ElMessage.warning('该装配件已被他人修改，已为你刷新列表')
      editingId.value = null
      void fetchData()
    } else {
      const msg = (e as { message?: string }).message ?? '保存失败'
      ElMessage.error(msg)
    }
  } finally {
    savingEdit.value = false
  }
}

// 2026-07-24：编辑态回车保存（与 PartsList 同款本地实现）
// 黑名单：搜索框 / popper 下拉 / 日期 picker
const ASM_ENTER_BLACKLIST = [
  '.filter-card', '.el-popper.is-light', '.el-select-dropdown',
  '.el-tree-select__popper', '.el-cascader__dropdown', '.el-date-picker',
]
function onEditEnterAsm(e: KeyboardEvent): void {
  if (e.key !== 'Enter') return
  if (editingId.value == null) return
  const target = e.target as HTMLElement | null
  if (target && ASM_ENTER_BLACKLIST.some((sel) => target.closest(sel))) return
  e.preventDefault()
  const row = items.value.find((r) => r.id === editingId.value)
  if (row) void saveEditAsm(row)
}

watch(editingId, (val) => {
  if (typeof document === 'undefined') return
  if (val != null) {
    document.addEventListener('keydown', onEditEnterAsm)
  } else {
    document.removeEventListener('keydown', onEditEnterAsm)
  }
})

onBeforeUnmount(() => {
  if (typeof document === 'undefined') return
  document.removeEventListener('keydown', onEditEnterAsm)
})

// 2026-07-24 v2：表格底部合计行（仅总价列求和）
function totalPriceSummary({ columns, data }: SummaryMethodProps): string[] {
  return columns.map((col, index) => {
    if (col.label === '总价') {
      const total = data.reduce((sum, row) => {
        const q = Number(row.quantity ?? 0)
        const p = Number(row.unit_price ?? 0)
        return sum + (Number.isFinite(q) && Number.isFinite(p) ? q * p : 0)
      }, 0)
      return total.toFixed(2)
    }
    // 第一列（序列号）放"合计"label，其他列空字符串
    if (index === 0) return '合计'
    return ''
  })
}

// ============ 筛选状态持久化（PR-I 2026-07-20）============
const { restore: restoreAsmFilter, clear: clearAsmFilter } =
  useListFilterPersist<SearchState>(
    'assemblies_list_filter',
    { search, sortBy, sortDir, pageSize },
  )

onMounted(() => {
  // 1) 优先尝试从 URL ?status=PENDING 注入（与新建后跳转保持一致）
  const q = route.query.status
  if (typeof q === 'string' && q in ASSEMBLY_STATUS_LABEL) {
    search.statuses = [q]
  } else {
    // 2) 否则从 localStorage 恢复上次的筛选 / 排序 / 分页大小
    const persisted = restoreAsmFilter()
    if (persisted) {
      search.keyword = persisted.search.keyword ?? search.keyword
      search.statuses = Array.isArray(persisted.search.statuses)
        ? persisted.search.statuses
        : search.statuses
      search.isUrgent = persisted.search.isUrgent ?? search.isUrgent
      search.customerId = persisted.search.customerId ?? search.customerId
      // localStorage 存的是 string，恢复时按 enum 字面量收敛（默认值兜底）
      sortBy.value = (SORT_PROP_MAP[persisted.sortBy]
        ? persisted.sortBy as AssemblySortKey
        : 'PLANNED_DELIVERY_DATE')
      sortDir.value = (persisted.sortDir === 'ASC' || persisted.sortDir === 'DESC'
        ? persisted.sortDir as SortDir
        : 'ASC')
      pageSize.value = persisted.pageSize
    }
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

  @include until(sm) {
    justify-content: center;
  }
}

/* 手机筛选抽屉（与 PartsList 同款） */
.mobile-filter {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.mf-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mf-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.mf-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mf-urgent {
  margin-top: 6px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color);
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