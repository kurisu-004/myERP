<!-- 报价一览页 — 外协报价 CRUD + MANAGER 审批
     (2026-07-16 仿 PartsList.vue 范式重排版：列头 popover 筛选 + 列头排序 + 分页 sizes)
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Filter, RefreshLeft, Search } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import {
  approveOutsourceQuote,
  createOutsourceQuote,
  listApprovedForSend,
  listOutsourceQuotes,
  rejectOutsourceQuote,
  softDeleteOutsourceQuote,
  submitOutsourceQuote,
  updateOutsourceQuote,
} from '@/api/outsource'
import { listCustomers, type Customer } from '@/api/customer'
import { listOutsourceCompanies } from '@/api/outsource'
import { listProcesses } from '@/api/process'
import { listParts } from '@/api/parts'
import { listPartFiles } from '@/api/assembly'
import { api } from '@/api/http'
import type { PartFileItem } from '@/types/part_file'
import type { PartListItem } from '@/types/parts'
import type { Process } from '@/types/process'
import { useAuthSession } from '@/composables/useAuthSession'
import { useCustomerTree } from '@/composables/useCustomerTree'
import {
  OUTSOURCE_QUOTE_STATUS_LABEL,
  OUTSOURCE_QUOTE_STATUS_TAG,
  type OutsourceQuote,
  type OutsourceQuoteStatus,
} from '@/types/outsource'
import {
  canApprove,
  canCreate,
  canEdit,
  canReject,
  canSoftDelete,
  rolesArrayToMap,
} from '@/utils/outsourceQuotePermissions'

const { user, hasRole } = useAuthSession()
const roleMap = computed(() => rolesArrayToMap(user.value?.roles ?? []))
const { isMobile } = useBreakpoint()
const createDlg = useDialogSize({ desktopWidth: 640, fullscreenOnMobile: true })
const reviewDlg = useDialogSize({ desktopWidth: 480 })
const previewDlg = useDialogSize({ desktopWidth: 900, fullscreenOnMobile: true })
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

/** 按角色注入默认 statuses：
 *  - CLERK 默认 DRAFT（待他提交审核的）
 *  - MANAGER 默认 SUBMITTED（待他审批的）
 *  - 其它角色不预选
 */
function defaultStatusesForRole(rm: ReturnType<typeof rolesArrayToMap>): OutsourceQuoteStatus[] {
  if (rm.MANAGER) return ['SUBMITTED']
  if (rm.CLERK) return ['DRAFT']
  return []
}

const { tree: customerTree } = useCustomerTree()

// ============================================================
// 筛选 / 排序 / 分页 状态
// ============================================================
interface SearchState {
  keyword: string
  statuses: OutsourceQuoteStatus[]
  customerId: string
}
function initialSearch(): SearchState {
  return { keyword: '', statuses: [], customerId: '' }
}
const search = reactive<SearchState>(initialSearch())

const statusOptions: { value: OutsourceQuoteStatus; label: string }[] = (
  Object.entries(OUTSOURCE_QUOTE_STATUS_LABEL) as [OutsourceQuoteStatus, string][]
).map(([value, label]) => ({ value, label }))

const statusFilterActive = computed(() => search.statuses.length > 0)
const customerFilterActive = computed(() => search.customerId !== '')

// 状态列头 popover（draft + 确定/重置）
const statusPopoverVisible = ref(false)
const statusDraft = ref<OutsourceQuoteStatus[]>([])

function syncStatusDraft(): void {
  statusDraft.value = [...search.statuses]
}
function resetStatusDraft(): void {
  statusDraft.value = []
  search.statuses = []
  statusPopoverVisible.value = false
  onSearch()
}
function confirmStatusFilter(): void {
  search.statuses = [...statusDraft.value]
  statusPopoverVisible.value = false
  onSearch()
}

// 客户列头 popover（draft + 确定/重置；用 el-tree-select 选 L1，自动展平）
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

// 手机筛选抽屉：复用桌面表头 popover 的草稿状态
const mobileFilterOpen = ref(false)
const anyFilterActive = computed(() => statusFilterActive.value || customerFilterActive.value)

function openMobileFilter(): void {
  syncStatusDraft()
  syncCustomerDraft()
  mobileFilterOpen.value = true
}
function confirmMobileFilter(): void {
  search.statuses = [...statusDraft.value]
  search.customerId = customerDraft.value ?? ''
  mobileFilterOpen.value = false
  onSearch()
}
function resetMobileFilter(): void {
  statusDraft.value = []
  customerDraft.value = null
  search.statuses = []
  search.customerId = ''
  mobileFilterOpen.value = false
  onSearch()
}

// ============================================================
// 表格 / 排序
// ============================================================
const items = ref<OutsourceQuote[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)

type SortKey = 'CREATED_AT' | 'PRICE' | 'REVIEWED_AT'
const sortBy = ref<SortKey>('CREATED_AT')
const sortDir = ref<'ASC' | 'DESC'>('DESC')

const SORT_PROP_MAP: Record<string, SortKey> = {
  part_serial_no: 'CREATED_AT',  // 默认按创建时间
  part_drawing_no: 'CREATED_AT',
  part_name: 'CREATED_AT',
  outsource_company_name: 'CREATED_AT',
  process_code: 'CREATED_AT',
  price: 'PRICE',
  customer_path: 'CREATED_AT',
}

type SortOrder = 'ascending' | 'descending'
const defaultSort = computed<{ prop: string; order: SortOrder }>(() => ({
  prop: 'part_serial_no',
  order: sortDir.value === 'ASC' ? 'ascending' : 'descending',
}))

const emptyText = computed(() => errorMsg.value ?? '暂无符合条件的报价')

function statusLabel(s: OutsourceQuoteStatus): string {
  return OUTSOURCE_QUOTE_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OutsourceQuoteStatus): 'info' | 'success' | 'warning' | 'danger' | '' {
  return OUTSOURCE_QUOTE_STATUS_TAG[s] ?? 'info'
}

/** 操作列自适应宽度：根据当前 items 中按钮数最多的行计算。
 *  每按钮约 76px（"提交审核" 4 字 + spacing），加 12px padding。
 *  默认 160px（无按钮 / 空列表时）防止抖动。 */
const actionColumnWidth = computed(() => {
  const maxBtns = items.value.reduce((max, q) => {
    let n = 0
    if (canEdit(q, roleMap.value)) n++
    if (canApprove(q, roleMap.value)) n++
    if (canReject(q, roleMap.value)) n++
    if (canSoftDelete(q, roleMap.value)) n++
    return Math.max(max, n)
  }, 0)
  return Math.max(160, maxBtns * 76 + 12)
})

/** 行点击触发图纸预览。
 *  Element Plus 默认 row-click 不会触发被嵌套按钮 click；操作列按钮
 *  的 click 事件已用 .stop 阻止冒泡。 */
function onRowClick(row: unknown): void {
  previewDrawing(row as OutsourceQuote)
}

/** 行 cursor: pointer（用 :row-class-name 把 hover cursor 加上） */
function drawingRowClass(): string {
  return 'quote-row-clickable'
}

function buildParams() {
  return {
    keyword: search.keyword.trim() || undefined,
    statuses: search.statuses.length > 0 ? [...search.statuses] : undefined,
    customer_id: search.customerId || undefined,
    sort_by: sortBy.value,
    sort_dir: sortDir.value,
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
}

async function refresh(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const r = await listOutsourceQuotes(buildParams())
    items.value = r.items
    total.value = r.total
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '加载报价列表失败'
    ElMessage.error(errorMsg.value)
  } finally {
    loading.value = false
  }
}

const onSearch = (): void => {
  page.value = 1
  void refresh()
}

function onSortChange({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void {
  if (!prop || !order) return
  sortBy.value = SORT_PROP_MAP[prop] ?? 'CREATED_AT'
  sortDir.value = order === 'ascending' ? 'ASC' : 'DESC'
  void refresh()
}

function onPageSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void refresh()
}

function onReset(): void {
  Object.assign(search, initialSearch())
  sortBy.value = 'CREATED_AT'
  sortDir.value = 'DESC'
  page.value = 1
  void refresh()
}

// ============================================================
// 零件 / 公司 / 工序 列表（弹窗用）
// ============================================================
const customers = ref<Customer[]>([])
const companies = ref<{ id: string; name: string }[]>([])
const processes = ref<Process[]>([])
const parts = ref<PartListItem[]>([])

async function loadLookups(): Promise<void> {
  try {
    customers.value = await listCustomers()
    const cs = await listOutsourceCompanies({ limit: 200 })
    companies.value = cs.items.map((c) => ({ id: c.id, name: c.name }))
    const ps = await listProcesses({ limit: 200 })
    processes.value = ps.items.filter((p) => p.category === 'OUTSOURCE')
    // 报价只针对可继续流转的零件：PENDING / IN_PROCESS；按创建时间倒序，最多 500 条
    const pt = await listParts({
      statuses: ['PENDING', 'IN_PROCESS'],
      sort_by: 'CREATED_AT',
      sort_dir: 'DESC',
      limit: 500,
    })
    parts.value = pt.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下拉数据加载失败')
  }
}

// ============================================================
// 图纸行内预览（2026-07-16）：行点击 / 图号链接 → 拉取该零件的 DRAWING
// → blob URL → 全屏 PDF / 图片预览
// ============================================================
const drawingPreviewVisible = ref(false)
const drawingPreviewUrl = ref<string | null>(null)
const drawingPreviewTitle = ref('图纸预览')
const drawingPreviewIsPdf = ref(false)
const drawingPreviewLoading = ref(false)
// 缓存：同一 part_id 重复点不重复拉取
const drawingCache = new Map<string, PartFileItem | null>()

const IMAGE_FILE_TYPES = new Set([
  'PNG', 'JPG', 'JPEG', 'GIF', 'BMP', 'TIF', 'TIFF', 'WEBP',
])
function isPdfType(t: string): boolean {
  return t.toUpperCase() === 'PDF'
}
function isImageType(t: string): boolean {
  return IMAGE_FILE_TYPES.has(t.toUpperCase())
}

async function ensureDrawing(partId: string): Promise<PartFileItem | null> {
  if (drawingCache.has(partId)) return drawingCache.get(partId) ?? null
  const files = await listPartFiles(partId, 'DRAWING')
  const f = files[0] ?? null
  drawingCache.set(partId, f)
  return f
}

async function previewDrawing(row: OutsourceQuote): Promise<void> {
  if (!row.part_id) return
  drawingPreviewLoading.value = true
  try {
    const f = await ensureDrawing(row.part_id)
    if (!f) {
      ElMessage.warning('该零件暂无图纸')
      return
    }
    const resp = await api.get<Blob>(
      `/files/${encodeURIComponent(f.id)}/content`,
      { responseType: 'blob' },
    )
    if (drawingPreviewUrl.value) URL.revokeObjectURL(drawingPreviewUrl.value)
    drawingPreviewUrl.value = URL.createObjectURL(resp.data)
    drawingPreviewTitle.value = `图纸预览 — ${row.part_drawing_no ?? ''} / ${row.part_name ?? ''}`
    drawingPreviewIsPdf.value = isPdfType(f.file_type)
    drawingPreviewVisible.value = true
  } catch (e) {
    ElMessage.error((e as Error).message ?? '图纸加载失败')
  } finally {
    drawingPreviewLoading.value = false
  }
}

function closeDrawingPreview(): void {
  drawingPreviewVisible.value = false
  if (drawingPreviewUrl.value) {
    URL.revokeObjectURL(drawingPreviewUrl.value)
    drawingPreviewUrl.value = null
  }
}

onBeforeUnmount(() => {
  if (drawingPreviewUrl.value) URL.revokeObjectURL(drawingPreviewUrl.value)
})

onMounted(async () => {
  await loadLookups()
  // 按角色注入默认 statuses（仅在用户尚未手动选过状态时生效）
  if (search.statuses.length === 0) {
    const defaults = defaultStatusesForRole(roleMap.value)
    if (defaults.length > 0) {
      search.statuses = [...defaults]
      statusDraft.value = [...defaults]
    }
  }
  await refresh()
})

// ============================================================
// 新建 / 提交 / 审批 / 拒绝 / 删除
// ============================================================
const showCreate = ref(false)
const createForm = reactive({
  part_id: '',
  outsource_company_id: '',
  process_id: '',
  price: '',
  note: '',
})
function openCreate(): void {
  createForm.part_id = ''
  createForm.outsource_company_id = ''
  createForm.process_id = ''
  createForm.price = ''
  createForm.note = ''
  showCreate.value = true
}
async function onCreate(): Promise<void> {
  if (!createForm.part_id || !createForm.outsource_company_id || !createForm.process_id) {
    ElMessage.warning('请填写零件 / 公司 / 工序')
    return
  }
  try {
    await createOutsourceQuote({
      part_id: createForm.part_id,
      outsource_company_id: createForm.outsource_company_id,
      process_id: createForm.process_id,
      price: createForm.price || '0',
      note: createForm.note || null,
    })
    ElMessage.success('已创建 DRAFT 报价')
    showCreate.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '创建失败')
  }
}

async function onSubmit(q: OutsourceQuote): Promise<void> {
  try {
    await submitOutsourceQuote(q.id)
    ElMessage.success('已提交审核')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  }
}

const showApprove = ref(false)
const showReject = ref(false)
const reviewNote = ref('')
const activeQuote = ref<OutsourceQuote | null>(null)
function openApprove(q: OutsourceQuote): void {
  activeQuote.value = q
  reviewNote.value = ''
  showApprove.value = true
}
function openReject(q: OutsourceQuote): void {
  activeQuote.value = q
  reviewNote.value = ''
  showReject.value = true
}
async function onApprove(): Promise<void> {
  if (!activeQuote.value) return
  try {
    await approveOutsourceQuote(activeQuote.value.id, {
      version: activeQuote.value.version,
      review_note: reviewNote.value || null,
    })
    ElMessage.success('已通过')
    showApprove.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '审批失败')
  }
}
async function onReject(): Promise<void> {
  if (!activeQuote.value || !reviewNote.value.trim()) {
    ElMessage.warning('请填写拒绝原因')
    return
  }
  try {
    await rejectOutsourceQuote(activeQuote.value.id, {
      version: activeQuote.value.version,
      review_note: reviewNote.value.trim(),
    })
    ElMessage.success('已拒绝')
    showReject.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '拒绝失败')
  }
}

async function onDelete(q: OutsourceQuote): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定要软删报价 #${q.id}（${OUTSOURCE_QUOTE_STATUS_LABEL[q.status]}）？`,
      '确认操作',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await softDeleteOutsourceQuote(q.id)
    ElMessage.success('已软删')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}
</script>

<template>
  <div class="page">
    <!-- 顶部 filter-card：仅保留 keyword + 重置 + 共 N 条 + 新建 -->
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="search.keyword"
          placeholder="序列号 / 图号 / 名称（前缀搜索）"
          clearable
          style="width: 280px"
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

        <el-button
          v-if="isMobile"
          :type="anyFilterActive ? 'primary' : 'default'"
          plain
          @click="openMobileFilter"
        >
          <el-icon><Filter /></el-icon>
          <span>筛选</span>
        </el-button>

        <el-button
          v-if="canCreate(roleMap)"
          type="success"
          @click="openCreate"
        >
          新建报价
        </el-button>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
      </div>
    </el-card>

    <el-card shadow="never">
      <ResponsiveList
        :items="items"
        :loading="loading"
        row-key="id"
        :empty-text="emptyText"
        stripe
        border
        size="small"
        :default-sort="defaultSort"
        :row-class-name="drawingRowClass"
        @sort-change="onSortChange"
        @row-click="onRowClick"
        @card-click="onRowClick"
      >
        <el-table-column
          prop="part_serial_no"
          label="序列号"
          width="100"
          sortable="custom"
          show-overflow-tooltip
        />

        <el-table-column
          prop="part_drawing_no"
          label="图号"
          width="120"
          sortable="custom"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <el-link
              v-if="(row as OutsourceQuote).part_drawing_no"
              type="primary"
              :underline="false"
              @click.stop="previewDrawing(row as OutsourceQuote)"
            >{{ (row as OutsourceQuote).part_drawing_no }}</el-link>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column
          prop="part_name"
          label="名称"
          min-width="180"
          sortable="custom"
          show-overflow-tooltip
        />

        <el-table-column
          prop="outsource_company_name"
          label="外协公司"
          width="160"
          sortable="custom"
          show-overflow-tooltip
        />

        <el-table-column
          prop="process_code"
          label="工序"
          width="100"
          sortable="custom"
        />

        <el-table-column
          prop="price"
          label="单价(元)"
          width="100"
          align="right"
          sortable="custom"
        />

        <!-- 状态列（无 sortable；用列头 popover 过滤） -->
        <el-table-column label="状态" width="110" align="center">
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
                  多选状态（提交确认后生效）
                </div>
                <el-checkbox-group v-model="statusDraft">
                  <el-checkbox
                    v-for="opt in statusOptions"
                    :key="opt.value"
                    :value="opt.value"
                    :label="opt.label"
                  />
                </el-checkbox-group>
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
              :type="(statusTagType((row as OutsourceQuote).status) || 'info') as 'info' | 'success' | 'warning' | 'danger'"
              size="small"
              effect="plain"
            >
              {{ statusLabel((row as OutsourceQuote).status) }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 客户列（无 sortable；用列头 popover 过滤 L1 客户） -->
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
            <span v-if="(row as OutsourceQuote).customer_path">{{ (row as OutsourceQuote).customer_path }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" :width="actionColumnWidth" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canEdit((row as OutsourceQuote), roleMap)"
              size="small"
              @click="onSubmit((row as OutsourceQuote))"
            >提交审核</el-button>
            <el-button
              v-if="canApprove((row as OutsourceQuote), roleMap)"
              size="small"
              type="success"
              @click="openApprove((row as OutsourceQuote))"
            >通过</el-button>
            <el-button
              v-if="canReject((row as OutsourceQuote), roleMap)"
              size="small"
              type="danger"
              @click="openReject((row as OutsourceQuote))"
            >拒绝</el-button>
            <el-button
              v-if="canSoftDelete((row as OutsourceQuote), roleMap)"
              size="small"
              type="danger"
              @click="onDelete((row as OutsourceQuote))"
            >删除</el-button>
          </template>
        </el-table-column>

        <template #card="{ row }">
          <div class="rl-card-head">
            <span class="rl-card-title">{{ (row as OutsourceQuote).part_name || '未命名零件' }}</span>
            <el-tag
              :type="(statusTagType((row as OutsourceQuote).status) || 'info') as 'info' | 'success' | 'warning' | 'danger'"
              size="small"
              effect="plain"
            >
              {{ statusLabel((row as OutsourceQuote).status) }}
            </el-tag>
          </div>
          <div class="rl-card-sub">
            图号 {{ (row as OutsourceQuote).part_drawing_no || '—' }} · 序列号 {{ (row as OutsourceQuote).part_serial_no || '—' }}
          </div>
          <div class="rl-kv">
            <div class="rl-kv__item">
              <span class="rl-kv__key">外协公司</span>
              <span class="rl-kv__val">{{ (row as OutsourceQuote).outsource_company_name || '—' }}</span>
            </div>
            <div class="rl-kv__item">
              <span class="rl-kv__key">工序</span>
              <span class="rl-kv__val">{{ (row as OutsourceQuote).process_code || '—' }}</span>
            </div>
            <div class="rl-kv__item">
              <span class="rl-kv__key">单价</span>
              <span class="rl-kv__val">{{ (row as OutsourceQuote).price }} 元</span>
            </div>
            <div class="rl-kv__item rl-kv__item--full">
              <span class="rl-kv__key">客户</span>
              <span class="rl-kv__val">{{ (row as OutsourceQuote).customer_path || '—' }}</span>
            </div>
          </div>
          <div class="rl-card-actions">
            <el-button
              v-if="canEdit((row as OutsourceQuote), roleMap)"
              size="small"
              @click.stop="onSubmit((row as OutsourceQuote))"
            >提交审核</el-button>
            <el-button
              v-if="canApprove((row as OutsourceQuote), roleMap)"
              size="small"
              type="success"
              @click.stop="openApprove((row as OutsourceQuote))"
            >通过</el-button>
            <el-button
              v-if="canReject((row as OutsourceQuote), roleMap)"
              size="small"
              type="danger"
              @click.stop="openReject((row as OutsourceQuote))"
            >拒绝</el-button>
            <el-button
              v-if="canSoftDelete((row as OutsourceQuote), roleMap)"
              size="small"
              type="danger"
              @click.stop="onDelete((row as OutsourceQuote))"
            >删除</el-button>
          </div>
        </template>
      </ResponsiveList>

      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100]"
          :total="total"
          :layout="paginationLayout"
          :pager-count="isMobile ? 5 : 7"
          background
          size="small"
          @current-change="refresh"
          @size-change="onPageSizeChange"
        />
      </div>
    </el-card>

    <!-- 新建 DRAFT 报价 -->
    <el-dialog
      v-model="showCreate"
      title="新建外协报价（DRAFT）"
      :width="createDlg.width.value"
      :top="createDlg.top.value"
      :fullscreen="createDlg.fullscreen.value"
    >
      <el-form label-width="100px">
        <el-form-item label="零件">
          <el-select
            v-model="createForm.part_id"
            filterable
            style="width:100%"
            placeholder="可报价零件（PENDING / 生产中）按创建时间倒序，最多 500 条"
          >
            <el-option
              v-for="p in parts"
              :key="p.id"
              :label="`${p.serial_no ?? '—'} | ${p.drawing_no ?? ''} | ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="外协公司">
          <el-select
            v-model="createForm.outsource_company_id"
            filterable
            style="width:100%"
          >
            <el-option
              v-for="c in companies"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="工序(OUTSOURCE)">
          <el-select
            v-model="createForm.process_id"
            filterable
            style="width:100%"
          >
            <el-option
              v-for="p in processes"
              :key="p.id"
              :label="`${p.code} ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="单价(元)">
          <el-input v-model="createForm.price" type="number" :precision="2" :step="0.01" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.note" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="onCreate">保存为 DRAFT</el-button>
      </template>
    </el-dialog>

    <!-- 通过 -->
    <el-dialog
      v-model="showApprove"
      title="审批通过"
      :width="reviewDlg.width.value"
      :top="reviewDlg.top.value"
    >
      <el-form label-width="100px">
        <el-form-item label="审批意见">
          <el-input v-model="reviewNote" type="textarea" placeholder="可留空" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showApprove = false">取消</el-button>
        <el-button type="success" @click="onApprove">通过</el-button>
      </template>
    </el-dialog>

    <!-- 拒绝 -->
    <el-dialog
      v-model="showReject"
      title="审批拒绝（必填原因）"
      :width="reviewDlg.width.value"
      :top="reviewDlg.top.value"
    >
      <el-form label-width="100px">
        <el-form-item label="拒绝原因" required>
          <el-input v-model="reviewNote" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showReject = false">取消</el-button>
        <el-button type="danger" @click="onReject">拒绝</el-button>
      </template>
    </el-dialog>

    <!-- 图纸行内预览（2026-07-16） -->
    <el-dialog
      v-model="drawingPreviewVisible"
      :title="drawingPreviewTitle"
      :width="previewDlg.width.value"
      :top="previewDlg.top.value"
      :fullscreen="previewDlg.fullscreen.value"
      :close-on-click-modal="false"
      :destroy-on-close="true"
      append-to-body
      @close="closeDrawingPreview"
    >
      <div v-if="drawingPreviewUrl" class="drawing-frame-wrap">
        <iframe
          v-if="drawingPreviewIsPdf"
          :src="drawingPreviewUrl"
          class="drawing-frame"
          title="PDF 图纸预览"
        />
        <el-image
          v-else
          :src="drawingPreviewUrl"
          :preview-src-list="[drawingPreviewUrl]"
          fit="contain"
          class="drawing-image"
        />
      </div>
      <p v-else class="muted">无可预览内容</p>
    </el-dialog>

    <!-- 手机筛选抽屉：承载桌面状态 / 客户列头筛选 -->
    <el-drawer
      v-model="mobileFilterOpen"
      title="筛选"
      direction="btt"
      size="72%"
    >
      <div class="mobile-filter">
        <div class="mf-section">
          <div class="mf-label">状态</div>
          <el-checkbox-group v-model="statusDraft" class="mf-status">
            <el-checkbox
              v-for="opt in statusOptions"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-checkbox-group>
        </div>
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
      </div>
      <template #footer>
        <el-button @click="resetMobileFilter">重置</el-button>
        <el-button type="primary" @click="confirmMobileFilter">确定</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style lang="scss" scoped>
.page {
  padding: 16px;
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

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;

  @include until(sm) {
    justify-content: center;
  }
}

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

.muted {
  color: var(--text-secondary);
}

// 2026-07-16：行点击 → 预览图纸；光标暗示
:deep(.el-table__row.quote-row-clickable) {
  cursor: pointer;
}

.drawing-frame-wrap {
  width: 100%;
  height: 70vh;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  justify-content: center;
}
.drawing-frame {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}
.drawing-image {
  max-width: 100%;
  max-height: 70vh;
}
</style>
