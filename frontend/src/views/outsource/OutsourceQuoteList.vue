<!-- 报价一览页 — 外协报价 CRUD + MANAGER 审批
     (2026-07-16 仿 PartsList.vue 范式重排版：列头 popover 筛选 + 列头排序 + 分页 sizes)
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Filter, RefreshLeft, Search } from '@element-plus/icons-vue'
import PdfViewer from '@/components/PdfViewer.vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  approveOutsourceQuote,
  createOutsourceQuote,
  listApprovedForSend,
  listCompaniesByProcess,
  listOutsourceQuotes,
  listQuotableParts,
  rejectOutsourceQuote,
  softDeleteOutsourceQuote,
  submitOutsourceQuote,
  updateOutsourceQuote,
} from '@/api/outsource'
import { listCustomers, type Customer } from '@/api/customer'
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
const route = useRoute()
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

/** 报价列表有效筛选状态（不含 legacy 数据状态） */
const ACTIVE_QUOTE_STATUSES: OutsourceQuoteStatus[] = ['DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED']

const statusOptions: { value: OutsourceQuoteStatus; label: string }[] = (
  Object.entries(OUTSOURCE_QUOTE_STATUS_LABEL) as [OutsourceQuoteStatus, string][]
).filter(([value]) => ACTIVE_QUOTE_STATUSES.includes(value))
  .map(([value, label]) => ({ value, label }))

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

// ============ 筛选状态持久化（2026-07-30 commit 4B）============
// 持久化 search / sortBy / sortDir / pageSize；page 不进快照。
// 优先级：URL ?statuses=  >  restore 快照  >  角色默认（DRAFT / SUBMITTED）
const { restore: restoreQuoteFilter } = useListStatePersist(
  'outsource_quote_list',
  { search, sortBy, sortDir, pageSize },
  { exclude: new Set(['page']) },
)

// ============ 列可见性 ============
// 「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'part_serial_no', label: '序列号' },
  { key: 'part_drawing_no', label: '图号' },
  { key: 'part_name', label: '名称' },
  { key: 'outsource_company_name', label: '外协公司' },
  { key: 'process_code', label: '工序' },
  { key: 'price', label: '外协报价' },
  { key: 'part_unit_price', label: '订单单价' },  // 2026-08-02 新增
  { key: 'status', label: '状态' },
  { key: 'customer', label: '客户' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'outsource_quote_list' })

const SORT_PROP_MAP: Record<string, SortKey> = {
  part_serial_no: 'CREATED_AT',  // 默认按创建时间
  part_drawing_no: 'CREATED_AT',
  part_name: 'CREATED_AT',
  outsource_company_name: 'CREATED_AT',
  process_code: 'CREATED_AT',
  price: 'PRICE',
  part_unit_price: 'CREATED_AT',  // 2026-08-02 新增（无对应 enum，按 CREATED_AT 兜底）
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
 *  Element Plus row-click 不会因嵌套按钮自动短路,
 *  操作列每个按钮必须显式 @click.stop 阻止冒泡（见下方操作列）。
 *  图号链接也用 @click.stop（见 drawing_no 列），但通过单独调用
 *  previewDrawing() 主动触发预览，不依赖 row-click。
 */
function onRowClick(row: unknown): void {
  previewDrawing(row as OutsourceQuote)
}

/** 行 cursor: pointer（用 :row-class-name 把 hover cursor 加上） +
 * 2026-08-04：加急行加 row-urgent 红底 */
function quoteRowClassName({ row }: { row: OutsourceQuote }): string {
  const cls = ['quote-row-clickable']
  if (row.is_urgent) cls.push('row-urgent')
  return cls.join(' ')
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

/**
 * 2026-07-29 PR-fix-0.2.0 dedup：折叠同一 (part_id, next_process_id) 的多批次行。
 * 后端 /quotable-parts 行=批次（批次化 contract），但 picker 下拉框里展示「同工单
 * 同外协工序」的多批次无意义——用户只为该 (part, process) 创建一份报价即可，发往
 * 任意批次都走这份报价。折叠后选中的 row 仍带 batch_id 等字段（仅展示时不再用）。
 *
 * Key 选择：`(part_id, next_process_id)`：
 * - 同一 part + 同一 next_process = "同一外协工序队列"，只留一行。
 * - 同一 part + 不同 next_process（工单有多种工序排队）应保留，让用户能为不同外协
 *   工序分别报价。
 *
 * 后端 contract 不变：API 仍返回每批一行（其他下游 OutsourceSendableItem 等仍按需
 * 消费批次），本函数仅是 picker 显示层的过滤。
 */
function dedupeByPartProcess(rows: PartListItem[]): PartListItem[] {
  const seen = new Set<string>()
  const out: PartListItem[] = []
  for (const r of rows) {
    const key = `${r.id}::${r.next_process_id ?? 'null'}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out
}

async function loadLookups(): Promise<void> {
  try {
    customers.value = await listCustomers()
    const ps = await listProcesses({ limit: 200 })
    processes.value = ps.items.filter((p) => p.category === 'OUTSOURCE')
    // PR-H 2026-07-28：新建报价 picker 改为「仅显示外协工序货架上的零件」
    // 旧版用 listParts({ statuses: ['PENDING','IN_PROCESS'], limit: 500 })；
    // 新版走专用端点 GET /outsource-quotes/quotable-parts
    // PR-fix-0.2.0 dedup：行=批次折叠到 (part_id, next_process_id)。
    const raw = await listQuotableParts({ limit: 500 })
    parts.value = dedupeByPartProcess(raw)
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
  // 先从 localStorage 恢复非 statuses 字段（keyword / customerId / sortBy / sortDir / pageSize）
  // —— 这些字段无 URL/角色默认优先级，直接 restore 即可。
  const persisted = restoreQuoteFilter()
  if (persisted) {
    if (persisted.search) Object.assign(search, persisted.search as Partial<SearchState>)
    if (typeof persisted.sortBy === 'string') sortBy.value = persisted.sortBy as SortKey
    if (typeof persisted.sortDir === 'string') sortDir.value = persisted.sortDir as 'ASC' | 'DESC'
    if (typeof persisted.pageSize === 'number') pageSize.value = persisted.pageSize as number
  }
  // 决定 statuses 的优先级（独立处理）：
  //   1) URL ?statuses= 逗号分隔（如 ?statuses=DRAFT,SUBMITTED）—— 最高优先
  //   2) restore() 快照中的 statuses（用户上次手动选的；上一步已 Object.assign 进 search）
  //   3) 角色默认（MANAGER→SUBMITTED / CLERK→DRAFT）
  const urlStatusesRaw = route.query.statuses
  const urlStatuses = typeof urlStatusesRaw === 'string'
    ? urlStatusesRaw.split(',').filter((s): s is OutsourceQuoteStatus =>
        ACTIVE_QUOTE_STATUSES.includes(s as OutsourceQuoteStatus))
    : []
  if (urlStatuses.length > 0) {
    search.statuses = [...urlStatuses]
    statusDraft.value = [...urlStatuses]
  } else if (search.statuses.length === 0) {
    // 上述 restore 已可能写回 search.statuses；只有仍为空才走角色默认
    const defaults = defaultStatusesForRole(roleMap.value)
    if (defaults.length > 0) {
      search.statuses = [...defaults]
      statusDraft.value = [...defaults]
    }
  } else {
    // restore 已写回 statuses → 同步 statusDraft
    statusDraft.value = [...search.statuses]
  }
  await refresh()
})

// ============================================================
// 新建 / 提交 / 审批 / 拒绝 / 删除
// ============================================================
const showCreate = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({
  part_id: '',
  outsource_company_id: '',
  process_id: '',
  price: '',
  note: '',
})
// 前端必填校验：4 个核心字段都必填，price 还需 > 0（镜像 schema/outsource_quote.py
// `OutsourceQuoteCreateRequest` 的 `gt=0`）。
const createRules: FormRules = {
  part_id: [{ required: true, message: '请选择零件', trigger: 'change' }],
  outsource_company_id: [
    { required: true, message: '请选择外协公司', trigger: 'change' },
  ],
  process_id: [
    { required: true, message: '请选择工序', trigger: 'change' },
  ],
  price: [
    { required: true, message: '请填写单价', trigger: 'blur' },
    {
      validator: (_rule, value: string, cb) => {
        const n = Number(value)
        if (value === '' || value == null || Number.isNaN(n) || n <= 0) {
          cb(new Error('单价必须大于 0'))
        } else {
          cb()
        }
      },
      trigger: 'blur',
    },
  ],
}
function openCreate(): void {
  createForm.part_id = ''
  createForm.outsource_company_id = ''
  createForm.process_id = ''
  createForm.price = ''
  createForm.note = ''
  showCreate.value = true
}

/** 新建报价对话框 — 工序变化时按工序反查外协公司（级联） */
const companiesLoading = ref(false)
async function loadCompaniesByProcess(processId: string): Promise<void> {
  if (!processId) {
    companies.value = []
    return
  }
  companiesLoading.value = true
  try {
    const cs = await listCompaniesByProcess(processId)
    companies.value = cs.map((c) => ({ id: c.id, name: c.name }))
  } catch (e) {
    companies.value = []
    ElMessage.error((e as Error).message ?? '外协公司加载失败')
  } finally {
    companiesLoading.value = false
  }
}

// 工序变化：级联刷新公司列表，并清掉之前已选的公司，避免脏数据
// 注意：此处 watch 必须在 createForm (reactive) 声明之后注册，
//       否则 watch() 同步调用 source getter 会撞到 const TDZ 抛 ReferenceError。
watch(
  () => createForm.process_id,
  (newPid) => {
    createForm.outsource_company_id = ''
    void loadCompaniesByProcess(newPid)
  },
)

/** PR-H 2026-07-28：选择零件后自动填工序（仅当 next_process_id 类别 = OUTSOURCE）。
 *  其他情况（INHOUSE / NULL）留空并提示。 */
function onCreatePartChange(partId: string): void {
  createForm.process_id = ''
  createForm.outsource_company_id = ''
  if (!partId) return
  const part = parts.value.find((p) => p.id === partId)
  if (!part?.next_process_id) {
    if (part) ElMessage.info('该零件未设置下一工序，请手动选择')
    return
  }
  // 仅当 next_process 类别 = OUTSOURCE 时自动填
  const proc = processes.value.find((p) => p.id === part.next_process_id)
  if (proc && proc.category === 'OUTSOURCE') {
    createForm.process_id = part.next_process_id
    // 触发 loadCompaniesByProcess 级联加载公司
    void loadCompaniesByProcess(part.next_process_id)
  } else {
    ElMessage.info('该零件的下一工序不是外协工序，请手动选择')
  }
}
async function onCreate(): Promise<void> {
  if (!createFormRef.value) return
  // el-form 校验：4 个必填字段 + price > 0；校验失败时 validate() reject，直接短路（红字提示）
  try {
    await createFormRef.value.validate()
  } catch {
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
        :row-class-name="quoteRowClassName"
        @sort-change="onSortChange"
        @row-click="onRowClick"
        @card-click="onRowClick"
      >
        <template #toolbar>
          <ColumnVisibilityPopover
            :defs="columnDefs"
            :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
            @reset="columnVisibility.showAll"
          />
        </template>
        <el-table-column
          v-if="columnVisibility.isVisible('part_serial_no')"
          prop="part_serial_no"
          label="序列号"
          min-width="100"
          sortable="custom"
          show-overflow-tooltip align="center"/>

        <el-table-column
          v-if="columnVisibility.isVisible('part_drawing_no')"
          prop="part_drawing_no"
          label="图号"
          min-width="120"
          sortable="custom"
          show-overflow-tooltip align="center">
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
          v-if="columnVisibility.isVisible('part_name')"
          prop="part_name"
          label="名称"
          min-width="180"
          sortable="custom"
          show-overflow-tooltip align="center"/>

        <el-table-column
          v-if="columnVisibility.isVisible('outsource_company_name')"
          prop="outsource_company_name"
          label="外协公司"
          min-width="160"
          sortable="custom"
          show-overflow-tooltip align="center"/>

        <el-table-column
          v-if="columnVisibility.isVisible('process_code')"
          prop="process_code"
          label="工序"
          min-width="100"
          sortable="custom" align="center"/>

        <el-table-column
          v-if="columnVisibility.isVisible('price')"
          prop="price"
          label="外协报价(元)"
          min-width="110"
          align="right"
          sortable="custom"
        />

        <!-- 2026-08-02 新增：所属零件的客户下单单价（与外协报价并列对比谈判空间） -->
        <el-table-column
          v-if="columnVisibility.isVisible('part_unit_price')"
          prop="part_unit_price"
          label="订单单价(元)"
          min-width="110"
          align="right"
          sortable="custom"
        >
          <template #default="{ row }">
            <span :class="{ muted: !(row as OutsourceQuote).part_unit_price }">
              {{ (row as OutsourceQuote).part_unit_price ?? '—' }}
            </span>
          </template>
        </el-table-column>

        <!-- 状态列（无 sortable；用列头 popover 过滤） -->
        <el-table-column
          v-if="columnVisibility.isVisible('status')"
          label="状态" min-width="110" align="center">
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
        <el-table-column
          v-if="columnVisibility.isVisible('customer')"
          label="客户" min-width="180" show-overflow-tooltip align="center">
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

        <el-table-column label="操作" :min-width="actionColumnWidth" fixed="right" align="center">
          <template #default="{ row }">
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
      title="新建外协报价"
      :width="createDlg.width.value"
      :top="createDlg.top.value"
      :fullscreen="createDlg.fullscreen.value"
    >
      <el-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-width="100px"
      >
        <el-form-item label="零件" prop="part_id">
          <el-select
            v-model="createForm.part_id"
            filterable
            style="width:100%"
            placeholder="可选报价零件（在外协工序货架上的在制件；按图号/名称筛选）"
            @change="onCreatePartChange"
          >
            <el-option
              v-for="p in parts"
              :key="p.id"
              :label="`${p.serial_no ?? '—'} | ${p.drawing_no ?? ''} | ${p.name} | ${p.shelf_code ?? ''}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="工序" prop="process_id">
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
        <el-form-item label="外协公司" prop="outsource_company_id">
          <el-select
            v-model="createForm.outsource_company_id"
            filterable
            :disabled="!createForm.process_id"
            :loading="companiesLoading"
            placeholder="请先选择工序"
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
        <el-form-item label="单价(元)" prop="price">
          <el-input v-model="createForm.price" type="number" :precision="2" :step="0.01" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.note" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="onCreate">保存草稿</el-button>
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
        <el-alert
          type="warning"
          :closable="false"
          style="margin-bottom: 12px"
        >
          通过后将自动拒绝该零件同工序的其他报价。
        </el-alert>
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
        <PdfViewer
          v-if="drawingPreviewIsPdf"
          :url="drawingPreviewUrl"


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

// 2026-08-04：加急行整行红底（与 PartsList / 看板同款）
:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}

.drawing-frame-wrap {
  width: 100%;
  height: 100%;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  justify-content: center;
}
.drawing-image {
  max-width: 100%;
  max-height: 70vh;
}
</style>
