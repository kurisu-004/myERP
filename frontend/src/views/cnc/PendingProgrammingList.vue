<!--
  PendingProgrammingList.vue — 待编程一览（status=PROGRAMMING 的零件）

  业务背景（2026-07-14）
  ====================
  - 菜单侧：CNC 编程员专属入口；侧栏只挂「待编程一览」（顶层菜单）。
  - 数据侧：调 GET /parts/pending-programming（status=PROGRAMMING 已硬编码于后端）。
  - 三个动作：
    * 「详情」 → 跳 /parts/{id}（PartDetail 页内有图纸下载 / G 代码上传 / 设定单上传）
    * 「下发到生产」 → 弹 el-dialog 同时选 PRODUCTION 货架 + 下一道工序，
      调 POST /parts/{id}/release-from-programming（PROGRAMMING → IN_PROCESS）。
      后端要求必须先上传 G_CODE + SETUP_SHEET，否则 400；前端 catch 后 ElMessage.error。
    * 「文件」 → 打开 el-drawer 按 DRAWING / 3D_MODEL / CAD_2D / G_CODE / SETUP_SHEET
      五类分组列出该零件的所有文件，每行一个「下载」按钮（用 PartFileItem 自带
      的 download_url 字段直接 window.open，sign 900s 临时 URL）。
  - 加急行整行红底 #fde2e2（与 PartsList / InspectionPending 同款）。
  - 自动刷新（10s）按需勾选。
-->
<template>
  <div class="pending-programming">
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

        <el-button @click="onSearch">
          <el-icon><RefreshLeft /></el-icon>
          <span>刷新</span>
        </el-button>

        <el-checkbox v-model="autoRefresh" @change="onAutoRefreshToggle">
          自动刷新（10s）
        </el-checkbox>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
        <el-tag v-else-if="!loading" type="info" effect="plain" size="small">
          当前无待编程零件
        </el-tag>
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
        :row-class-name="rowClassName"
        :empty-text="emptyText"
      >
        <el-table-column
          prop="serial_no"
          label="序列号"
          width="110"
          fixed="left"
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
          show-overflow-tooltip
        />

        <el-table-column
          prop="name"
          label="名称"
          min-width="200"
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
        />

        <el-table-column label="客户" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.customer_path">{{ row.customer_path }}</span>
            <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              @click="$router.push(`/parts/${row.id}`)"
            >详情</el-button>
            <el-button
              link
              type="success"
              size="small"
              :loading="row._releasing"
              @click="openReleaseDialog(row as PartListItem)"
            >下发</el-button>
            <el-button
              link
              type="warning"
              size="small"
              @click="openFilesDrawer(row as PartListItem)"
            >文件</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>
    </div>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        background
        size="small"
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 下发到生产 对话框：选目标生产货架 + 下一道工序 -->
    <el-dialog
      v-model="releaseDialogVisible"
      title="下发到生产 — 选择目标货架与下一道工序"
      width="560px"
      :close-on-click-modal="false"
      @closed="onReleaseDialogClosed"
    >
      <div v-if="releaseTarget" class="release-summary">
        <div><strong>流水号：</strong>{{ releaseTarget.serial_no || '—' }}</div>
        <div><strong>图号：</strong>{{ releaseTarget.drawing_no }}</div>
        <div><strong>名称：</strong>{{ releaseTarget.name }}</div>
      </div>

      <el-form label-width="110px" style="margin-top: 12px">
        <el-form-item label="目标生产货架" required>
          <el-radio-group
            v-model="releaseShelfId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto"
          >
            <el-radio
              v-for="s in productionShelves"
              :key="s.id"
              :value="String(s.id)"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-radio>
            <span v-if="productionShelves.length === 0" class="muted">
              没有可用生产货架
            </span>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="下一道工序" required>
          <el-radio-group
            v-model="releaseProcessId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto"
          >
            <el-radio
              v-for="p in processes"
              :key="p.id"
              :value="String(p.id)"
            >
              {{ p.code }} — {{ p.name }}
              <el-tag
                :type="p.category === 'INHOUSE' ? 'success' : 'info'"
                size="small"
                effect="plain"
                style="margin-left: 4px"
              >
                {{ p.category === 'INHOUSE' ? '自产' : '外协' }}
              </el-tag>
            </el-radio>
            <span v-if="processes.length === 0" class="muted">
              没有工序配置，请先在「设置 → 工序管理」中新增
            </span>
          </el-radio-group>
        </el-form-item>

        <el-alert
          type="info"
          :closable="false"
          title="下发后零件进入 IN_PROCESS / ON_SHELF 状态；必须先上传 G_CODE 与 SETUP_SHEET，否则后端会返回 400。"
          show-icon
        />
      </el-form>

      <template #footer>
        <el-button @click="releaseDialogVisible = false">取消</el-button>
        <el-button
          type="success"
          :loading="releaseSubmitting"
          :disabled="!releaseShelfId || !releaseProcessId"
          @click="onReleaseConfirm"
        >确认下发</el-button>
      </template>
    </el-dialog>

    <!-- 文件 抽屉：按 kind 分组列出 + 下载 -->
    <el-drawer
      v-model="filesDrawerVisible"
      direction="rtl"
      size="520px"
      :title="`文件 — ${filesDrawerTarget?.serial_no || filesDrawerTarget?.drawing_no || ''}`"
      :close-on-click-modal="false"
      @closed="onFilesDrawerClosed"
    >
      <div v-if="filesLoading" v-loading="true" class="files-loading"></div>
      <template v-else>
        <div v-if="groupedFiles.length === 0" class="files-empty">
          <el-empty description="该零件暂无任何文件" />
        </div>
        <div
          v-for="group in groupedFiles"
          :key="group.kind"
          class="files-group"
        >
          <div class="files-group-title">
            <el-icon><FolderOpened /></el-icon>
            <span>{{ group.title }}</span>
            <el-tag size="small" effect="plain" type="info">{{ group.items.length }}</el-tag>
          </div>
          <ul class="files-list">
            <li v-for="f in group.items" :key="f.id" class="file-item">
              <div class="file-meta">
                <el-icon><Document /></el-icon>
                <span class="file-name">{{ f.original_filename }}</span>
                <span class="file-size muted">{{ formatBytes(f.file_size) }}</span>
              </div>
              <el-button
                link
                type="primary"
                size="small"
                @click="onDownload(f)"
              >下载</el-button>
            </li>
          </ul>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Document,
  FolderOpened,
  RefreshLeft,
  Search,
} from '@element-plus/icons-vue'
import {
  listPendingProgramming,
  releaseFromProgramming,
} from '@/api/parts'
import { listPartFiles } from '@/api/assembly'
import { listShelves } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import type { PartListItem } from '@/types/parts'
import type { PartFileItem, PartFileKind } from '@/types/part_file'
import type { Shelf } from '@/types/shelf'
import type { Process } from '@/types/process'

// ============ 列表状态 ============
interface RowState extends PartListItem {
  _releasing?: boolean
}

const items = ref<RowState[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)

const search = reactive({ keyword: '' })

const emptyText = computed(() => errorMsg.value ?? '暂无待编程零件')

function rowClassName({ row }: { row: PartListItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const resp = await listPendingProgramming({
      keyword: search.keyword.trim() || undefined,
      sort_by: 'PLANNED_DELIVERY_DATE',
      sort_dir: 'ASC',
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    items.value = resp.items
    total.value = resp.total
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
  fetchList()
}

function onPageSizeChange(): void {
  page.value = 1
  fetchList()
}

// ============ 自动刷新 ============
const autoRefresh = ref(false)
let autoRefreshTimer: number | null = null

function onAutoRefreshToggle(val: string | number | boolean): void {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
  if (val) {
    autoRefreshTimer = window.setInterval(() => {
      fetchList()
    }, 10_000)
  }
}

onBeforeUnmount(() => {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer)
  }
})

// ============ 下发到生产 对话框 ============
const releaseDialogVisible = ref(false)
const releaseTarget = ref<RowState | null>(null)
const releaseShelfId = ref<string>('')
const releaseProcessId = ref<string>('')
const releaseSubmitting = ref(false)
const productionShelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])

async function loadProductionShelves(): Promise<void> {
  try {
    const resp = await listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 })
    productionShelves.value = resp.items
  } catch (e) {
    ElMessage.error(`加载生产货架失败：${(e as Error).message}`)
    productionShelves.value = []
  }
}

async function loadProcesses(): Promise<void> {
  try {
    const resp = await listProcesses({ limit: 200 })
    processes.value = resp.items
  } catch (e) {
    ElMessage.error(`加载工序失败：${(e as Error).message}`)
    processes.value = []
  }
}

async function openReleaseDialog(row: RowState): Promise<void> {
  releaseTarget.value = row
  releaseShelfId.value = ''
  releaseProcessId.value = ''
  releaseDialogVisible.value = true
  await Promise.all([
    productionShelves.value.length === 0 ? loadProductionShelves() : Promise.resolve(),
    processes.value.length === 0 ? loadProcesses() : Promise.resolve(),
  ])
}

function onReleaseDialogClosed(): void {
  releaseTarget.value = null
  releaseShelfId.value = ''
  releaseProcessId.value = ''
}

async function onReleaseConfirm(): Promise<void> {
  if (!releaseTarget.value || !releaseShelfId.value || !releaseProcessId.value) return
  const row = releaseTarget.value
  const shelfCode =
    productionShelves.value.find((s) => String(s.id) === releaseShelfId.value)?.code ?? ''
  const processCode =
    processes.value.find((p) => String(p.id) === releaseProcessId.value)?.code ?? ''
  try {
    await ElMessageBox.confirm(
      `确认下发「${row.name}」（${row.serial_no || row.drawing_no}）到生产货架 ${shelfCode}，下一道工序 ${processCode}？`,
      '下发到生产',
      { type: 'success', confirmButtonText: '确认下发', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  row._releasing = true
  releaseSubmitting.value = true
  try {
    await releaseFromProgramming(row.id, releaseShelfId.value, releaseProcessId.value)
    ElMessage.success(
      `零件 ${row.serial_no || row.drawing_no} 已下发到生产货架 ${shelfCode}`,
    )
    releaseDialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error(`下发失败：${(e as Error).message}`)
  } finally {
    row._releasing = false
    releaseSubmitting.value = false
  }
}

// ============ 文件 抽屉 ============
const filesDrawerVisible = ref(false)
const filesDrawerTarget = ref<PartListItem | null>(null)
const filesLoading = ref(false)
const filesByKind = ref<Record<PartFileKind, PartFileItem[]>>({
  DRAWING: [],
  '3D_MODEL': [],
  G_CODE: [],
  SETUP_SHEET: [],
  ASSEMBLY_MASTER: [],
  CAD_2D: [],
})

const KIND_TITLE: Record<PartFileKind, string> = {
  DRAWING: '图纸',
  '3D_MODEL': '3D 模型',
  G_CODE: 'G 代码',
  SETUP_SHEET: 'CNC 设定单',
  ASSEMBLY_MASTER: '装配体总装图',
  CAD_2D: 'CAD 源文件',
}

// 展示顺序：DRAWING / 3D_MODEL / CAD_2D / G_CODE / SETUP_SHEET
const KIND_DISPLAY_ORDER: PartFileKind[] = [
  'DRAWING', '3D_MODEL', 'CAD_2D', 'G_CODE', 'SETUP_SHEET',
]

const groupedFiles = computed(() =>
  KIND_DISPLAY_ORDER
    .map((kind) => ({
      kind,
      title: KIND_TITLE[kind],
      items: filesByKind.value[kind] ?? [],
    }))
    .filter((g) => g.items.length > 0),
)

async function openFilesDrawer(row: PartListItem): Promise<void> {
  filesDrawerTarget.value = row
  filesDrawerVisible.value = true
  filesLoading.value = true
  // 重置
  filesByKind.value = {
    DRAWING: [],
    '3D_MODEL': [],
    G_CODE: [],
    SETUP_SHEET: [],
    ASSEMBLY_MASTER: [],
    CAD_2D: [],
  }
  try {
    // 并发拉取 5 类文件
    const kinds: PartFileKind[] = [
      'DRAWING', '3D_MODEL', 'CAD_2D', 'G_CODE', 'SETUP_SHEET',
    ]
    const results = await Promise.allSettled(
      kinds.map((k) => listPartFiles(row.id, k)),
    )
    kinds.forEach((k, i) => {
      const r = results[i]
      if (r.status === 'fulfilled') {
        filesByKind.value[k] = r.value
      } else {
        filesByKind.value[k] = []
        // 单类失败不阻塞 drawer；累计到 console 方便排查
        // eslint-disable-next-line no-console
        console.warn(`listPartFiles(${row.id}, ${k}) failed:`, r.reason)
      }
    })
  } finally {
    filesLoading.value = false
  }
}

function onFilesDrawerClosed(): void {
  filesDrawerTarget.value = null
}

function onDownload(f: PartFileItem): void {
  // PartFileItem.download_url 是后端在 list 响应里同步签发的临时 URL（900s TTL）
  if (!f.download_url) {
    ElMessage.error('该文件下载链接尚未签发，请稍后重试')
    return
  }
  window.open(f.download_url, '_blank', 'noopener,noreferrer')
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}

onMounted(() => {
  fetchList()
})
</script>

<style scoped>
.pending-programming {
  padding: 0;
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
  margin-top: 12px;
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
:deep(.row-urgent) {
  background: #fde2e2 !important;
}
:deep(.row-urgent td) {
  background: #fde2e2 !important;
}
.release-summary {
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
  padding: 10px 14px;
  line-height: 1.8;
  font-size: 13px;
}
.files-loading {
  min-height: 240px;
}
.files-empty {
  padding: 40px 0;
}
.files-group {
  margin-bottom: 18px;
}
.files-group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
  color: var(--text-primary);
}
.files-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-radius: 4px;
}
.file-item:hover {
  background: #f5f7fa;
}
.file-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}
.file-name {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.file-size {
  font-size: 12px;
  margin-left: 8px;
  flex-shrink: 0;
}
</style>