<!--
  PartsList.vue

  零件一览表。

  - 顶部：图号/名称搜索框 + Reset 按钮 + 共 N 条
  - 所有筛选（状态、加急、客户）下沉到 el-table-column 表头内的 popover
  - 序列号/图号/名称/计划交期 均可点表头排序；默认按计划交期升序
  - is_urgent=true 的行整行红底 #fde2e2（dashboard 同款）
  - 下一道工序 / 装配 列已移除（信息放到详情页）
-->
<template>
  <div class="parts-list">
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

        <el-button @click="onReset">
          <el-icon><RefreshLeft /></el-icon>
          <span>重置</span>
        </el-button>

        <el-button v-if="!isInspector" @click="router.push('/parts/new/bid-import')">
          <el-icon><Document /></el-icon>
          <span>从应标 Excel 导入</span>
        </el-button>

        <!-- 批量打印图纸 toggle（2026-07-17 接入；2026-07-20 INSPECTOR 不可见） -->
        <template v-if="!isInspector">
          <el-button
            v-if="!batchMode"
            type="success"
            plain
            @click="onEnterBatchMode"
          >
            <el-icon><Printer /></el-icon>
            <span>批量打印图纸</span>
          </el-button>
          <el-button
            v-else
            type="warning"
            @click="onExitBatchMode"
          >
            <el-icon><Close /></el-icon>
            <span>退出批量模式</span>
          </el-button>
        </template>

        <el-tag v-if="isCncProgrammer" type="warning" effect="plain" size="small">
          编程员视图：默认查看「编程中」零件
        </el-tag>
        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
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
        @sort-change="onSortChange"
        @selection-change="onSelectionChange"
        :empty-text="emptyText"
      >
        <el-table-column
          v-if="batchMode"
          type="selection"
          width="55"
          :reserve-selection="true"
        />
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
          label="图号"
          width="130"
          fixed="left"
          sortable="custom"
          show-overflow-tooltip
        />

        <el-table-column
          prop="name"
          label="名称"
          min-width="200"
          sortable="custom"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <router-link :to="`/parts/${row.id}`" class="name-link">
              {{ row.name }}
            </router-link>
          </template>
        </el-table-column>

        <el-table-column prop="quantity" label="数量" width="80" align="right" />

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
                :width="220"
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
                    v-for="opt in statusOptions"
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
            <el-tag
              :type="statusTagType(row.status)"
              effect="plain"
              size="small"
            >
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="客户" min-width="180" show-overflow-tooltip>
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
                    :class="{ active: search.customerId !== '' }"
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
            <span v-if="row.customer_path">{{ row.customer_path }}</span>
            <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="所在位置" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.location === 'PRODUCTION_SHELF' && row.shelf_code">
              货架 {{ row.shelf_code }}
            </span>
            <span v-else-if="row.location === 'INSPECTION_SHELF' && row.shelf_code">
              品检 {{ row.shelf_code }}
            </span>
            <span v-else-if="row.location === 'WORKER' && row.worker_name">
              {{ row.worker_name }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
            <el-button
              v-if="!isInspector && row.status === 'PENDING'"
              link
              type="success"
              size="small"
              @click="onDispatch(row as PartListItem)"
            >下发</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>
    </div>

    <!-- 批量打印图纸 — 底部 action bar（2026-07-17；2026-07-20 INSPECTOR 不可见） -->
    <div v-if="!isInspector && batchMode" class="batch-bar">
      <div class="bar-info">
        <span>已选 <strong>{{ selectedRows.length }}</strong> 件</span>
        <el-button link size="small" @click="onSelectAllPage">全选当前页</el-button>
        <el-button link size="small" @click="onClearSelection">清空选择</el-button>
      </div>
      <el-button
        type="primary"
        :loading="batchPrinting"
        :disabled="selectedRows.length === 0"
        @click="onBatchPrint"
      >
        <el-icon><Printer /></el-icon>
        <span>打印预览（{{ selectedRows.length }} 件）</span>
      </el-button>
    </div>

    <!-- 隐藏 iframe：批量打印用（仿 FileListCard.vue 的 print 实现） -->
    <iframe
      ref="batchPrintIframeRef"
      style="position: fixed; right: 0; bottom: 0; width: 1px; height: 1px; border: 0; opacity: 0; pointer-events: none;"
      title="批量打印预览"
    />

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        background
        size="small"
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 下发对话框（沿用旧 PartsList 的下发流程） -->
    <el-dialog
      v-model="dispatchVisible"
      :title="dispatchMode === 'cnc' ? '发送至 CNC 编程' : '下发零件'"
      width="480px"
      @closed="onDispatchClosed"
    >
      <el-form label-width="96px">
        <el-form-item label="下发方式">
          <el-radio-group v-model="dispatchMode">
            <el-radio value="direct">直接下到生产货架</el-radio>
            <el-radio value="cnc">发送至 CNC 编程</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="dispatchMode === 'direct'">
          <el-form-item label="目标货架" required>
            <el-select
              v-model="dispatchShelfId"
              placeholder="选择生产货架"
              style="width: 100%"
              filterable
            >
              <el-option
                v-for="s in filteredShelves"
                :key="s.id"
                :label="s.name"
                :value="s.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="下一道工序" required>
            <el-select
              v-model="dispatchNextProcessId"
              placeholder="选择工序（必填）"
              style="width: 100%"
              filterable
            >
              <el-option
                v-for="p in filteredProcesses"
                :key="p.id"
                :label="`${p.code} / ${p.name}`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-else>
          <el-alert
            type="info"
            :closable="false"
            title="将零件发送至 CNC 编程环节，零件状态变为「编程中」。"
            description="CNC 编程员在「待编程一览」中下载图纸、上传 G 代码后，会再下发到生产货架。"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dispatchVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="dispatchSubmitting"
          :disabled="dispatchMode === 'direct' && (!dispatchShelfId || !dispatchNextProcessId)"
          @click="onDispatchConfirm"
        >
          {{ dispatchMode === 'cnc' ? '发送至 CNC 编程' : '确认下发' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Close,
  Document,
  Filter,
  Printer,
  RefreshLeft,
  Search,
} from '@element-plus/icons-vue'
import {
  listParts,
  placeOnShelf,
  printPartDrawingBatch,
  sendToProgramming,
  type ListPartsParams,
} from '@/api/parts'
import type { PartListItem, PartSortKey, SortDir } from '@/types/parts'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { Process } from '@/types/process'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  PART_SORT_PROP_MAP,
  type OrderStatus,
} from '@/types/parts'
import { useAuthSession } from '@/composables/useAuthSession'
import { usePermissions } from '@/composables/usePermissions'
import { useCustomerTree } from '@/composables/useCustomerTree'
import { useListFilterPersist } from '@/composables/useListFilterPersist'

// ============ 角色 & 默认筛选 ============
const { hasRole } = useAuthSession()
const isCncProgrammer = hasRole('CNC_PROGRAMMER')
// PR-I 2026-07-20：INSPECTOR 看不到导入 / 批量打印 / 下发按钮
const { isInspector } = usePermissions()
const { tree: customerTree } = useCustomerTree()
const route = useRoute()
const router = useRouter()

interface SearchState {
  keyword: string
  statuses: OrderStatus[]
  isUrgent: boolean | null
  customerId: string
}
function initialSearch(): SearchState {
  return {
    keyword: '',
    statuses: isCncProgrammer
      ? ['PROGRAMMING']
      : ['IN_PROCESS', 'REPAIRING'],
    isUrgent: null,
    customerId: '',
  }
}
const search = reactive<SearchState>(initialSearch())

const statusOptions: { value: OrderStatus; label: string }[] = (
  Object.keys(ORDER_STATUS_LABEL) as OrderStatus[]
).map((v) => ({ value: v, label: ORDER_STATUS_LABEL[v] }))

const statusFilterActive = computed(
  () => search.statuses.length > 0 || search.isUrgent === true,
)
const customerFilterActive = computed(() => search.customerId !== '')

// ============ 状态列头 popover（draft + 确定/重置） ============
// draft 完全用 OrderStatus 类型（用 string 存「仅加急」标记已删除）；
// 加急选项是独立 checkbox，不再混入 status 多选。
const statusPopoverVisible = ref(false)
const statusDraft = ref<OrderStatus[]>([])
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
  // 只装纯 OrderStatus 与 boolean，绝不混入 marker
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

// ============ 表格 / 排序 ============
const items = ref<PartListItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)
const sortBy = ref<PartSortKey>('PLANNED_DELIVERY_DATE')
const sortDir = ref<SortDir>('ASC')

// ============ 批量打印（2026-07-17 接入）============
const batchMode = ref(false)
const selectedRows = ref<PartListItem[]>([])
const batchPrinting = ref(false)
const batchPrintIframeRef = ref<HTMLIFrameElement | null>(null)
let batchPrintBlobUrl = ''

function onEnterBatchMode(): void {
  batchMode.value = true
  selectedRows.value = []
}
function onExitBatchMode(): void {
  batchMode.value = false
  selectedRows.value = []
}
function onSelectionChange(rows: PartListItem[]): void {
  selectedRows.value = rows
}
function onSelectAllPage(): void {
  // el-table 默认全选仅当前页；这里把当前页 items 视为全选
  selectedRows.value = [...items.value]
}
function onClearSelection(): void {
  selectedRows.value = []
}

async function onBatchPrint(): Promise<void> {
  if (selectedRows.value.length === 0) return
  batchPrinting.value = true
  try {
    const ids = selectedRows.value.map((r) => r.id)
    const blob = await printPartDrawingBatch(ids)
    if (batchPrintBlobUrl) URL.revokeObjectURL(batchPrintBlobUrl)
    batchPrintBlobUrl = URL.createObjectURL(blob)
    const iframe = batchPrintIframeRef.value
    if (!iframe) {
      ElMessage.error('打印 iframe 未挂载，请刷新页面后重试')
      return
    }
    iframe.src = batchPrintBlobUrl
    iframe.onload = () => {
      try {
        iframe.contentWindow?.focus()
        iframe.contentWindow?.print()
      } catch {
        // sandbox / cross-origin 等极端情况下 fallback 到新窗口打印
        const w = window.open(batchPrintBlobUrl, '_blank')
        if (w) w.print()
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '批量打印失败')
  } finally {
    setTimeout(() => { batchPrinting.value = false }, 800)
  }
}

onBeforeUnmount(() => {
  if (batchPrintBlobUrl) {
    URL.revokeObjectURL(batchPrintBlobUrl)
    batchPrintBlobUrl = ''
  }
})

const SORT_PROP_MAP: Record<string, PartSortKey> = PART_SORT_PROP_MAP

type SortOrder = 'ascending' | 'descending'
const defaultSort = computed<{ prop: string; order: SortOrder }>(() => ({
  prop: 'planned_delivery_date',
  order: sortDir.value === 'ASC' ? 'ascending' : 'descending',
}))

const emptyText = computed(() => errorMsg.value ?? '暂无符合条件的零件')

function statusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}

function rowClassName({ row }: { row: PartListItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

function buildParams(): ListPartsParams {
  return {
    customer_id: search.customerId || undefined,
    statuses: search.statuses.length > 0 ? search.statuses : undefined,
    is_urgent: search.isUrgent ?? undefined,
    keyword: search.keyword.trim() || undefined,
    sort_by: sortBy.value,
    sort_dir: sortDir.value,
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const resp = await listParts(buildParams())
    items.value = resp.items
    total.value = resp.total
    // 批量模式下：剔除已不在当前页的失效勾选（仿 DeliveryNoteNew 模式）
    if (batchMode.value) {
      const validIds = new Set(items.value.map((r) => r.id))
      selectedRows.value = selectedRows.value.filter((r) => validIds.has(r.id))
    }
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
    ElMessage.error(errorMsg.value)
  } finally {
    loading.value = false
  }
}

const onSearch = (): void => {
  page.value = 1
  void fetchList()
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
  void fetchList()
}

function onPageSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void fetchList()
}

// ============ 筛选状态持久化（PR-I 2026-07-20）============
const { restore: restorePartsFilter, clear: clearPartsFilter } =
  useListFilterPersist<SearchState>(
    'parts_list_filter',
    { search, sortBy, sortDir, pageSize },
  )

function onReset(): void {
  Object.assign(search, initialSearch())
  sortBy.value = 'PLANNED_DELIVERY_DATE'
  sortDir.value = 'ASC'
  page.value = 1
  clearPartsFilter()
  void fetchList()
}

onMounted(() => {
  // 1) 优先尝试从 URL ?status=PENDING 注入（与批量新建后跳转保持一致）
  const q = route.query.status
  if (typeof q === 'string' && q in ORDER_STATUS_LABEL) {
    search.statuses = [q as OrderStatus]
  } else {
    // 2) 否则从 localStorage 恢复上次的筛选 / 排序 / 分页大小
    const persisted = restorePartsFilter()
    if (persisted) {
      search.keyword = persisted.search.keyword ?? search.keyword
      search.statuses = Array.isArray(persisted.search.statuses)
        ? persisted.search.statuses
        : search.statuses
      search.isUrgent = persisted.search.isUrgent ?? search.isUrgent
      search.customerId = persisted.search.customerId ?? search.customerId
      // localStorage 存的是 string，恢复时按合法值收敛（默认值兜底）
      sortBy.value = (SORT_PROP_MAP[persisted.sortBy]
        ? persisted.sortBy as PartSortKey
        : 'PLANNED_DELIVERY_DATE')
      sortDir.value = (persisted.sortDir === 'ASC' || persisted.sortDir === 'DESC'
        ? persisted.sortDir as SortDir
        : 'ASC')
      pageSize.value = persisted.pageSize
    }
  }
  void fetchList()
})

// ============ 下发对话框 ============
const shelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])
const dispatchVisible = ref(false)
const dispatchShelfId = ref<string | null>(null)
const dispatchNextProcessId = ref<string | null>(null)
const dispatchPartId = ref<string | null>(null)
const dispatchSubmitting = ref(false)
const dispatchMode = ref<'direct' | 'cnc'>('direct')
// 2026-07-17：useShelfProcessFilter 双向收窄货架/工序下拉
const {
  filteredShelves,
  filteredProcesses,
  load: loadShelfProcessMap,
} = useShelfProcessFilter(
  shelves,
  processes,
  dispatchShelfId,
  dispatchNextProcessId,
)

async function onDispatch(row: PartListItem): Promise<void> {
  dispatchPartId.value = row.id
  dispatchShelfId.value = null
  dispatchNextProcessId.value = null
  dispatchMode.value = 'direct'
  try {
    const [shelfResp, procResp] = await Promise.all([
      listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 }),
      listProcesses({ limit: 200 }),
    ])
    shelves.value = shelfResp.items
    processes.value = procResp.items
    // 2026-07-17：弹窗打开后异步加载映射（不阻塞 dialog 出现）
    void loadShelfProcessMap()
  } catch {
    shelves.value = []
    processes.value = []
  }
  dispatchVisible.value = true
}

function onDispatchClosed(): void {
  dispatchPartId.value = null
  dispatchShelfId.value = null
  dispatchNextProcessId.value = null
  dispatchMode.value = 'direct'
}

async function onDispatchConfirm(): Promise<void> {
  if (!dispatchPartId.value) return
  if (dispatchMode.value === 'direct'
      && (!dispatchShelfId.value || !dispatchNextProcessId.value)) return
  dispatchSubmitting.value = true
  try {
    if (dispatchMode.value === 'cnc') {
      await sendToProgramming(dispatchPartId.value)
      ElMessage.success('已发送至 CNC 编程')
    } else {
      await placeOnShelf(
        dispatchPartId.value, dispatchShelfId.value!, dispatchNextProcessId.value!,
      )
      ElMessage.success('下发成功')
    }
    dispatchVisible.value = false
    void fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下发失败')
  } finally {
    dispatchSubmitting.value = false
  }
}
</script>

<style lang="scss" scoped>
.parts-list {
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

/* 批量打印底部 action bar（仿 DeliveryNoteNew 范式） */
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  padding: 10px 14px;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 6px;
}
.batch-bar .bar-info {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #303133;
  font-size: 13px;
}
.batch-bar .bar-info strong {
  color: #409eff;
  font-weight: 600;
}

.muted {
  color: var(--text-secondary);
}

.name-link {
  color: var(--primary-color);
  text-decoration: none;
  &:hover {
    text-decoration: underline;
  }
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

// 加急行：dashboard 同款红底 #fde2e2（与默认 .el-table 浅灰底可叠加）
:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}
</style>
