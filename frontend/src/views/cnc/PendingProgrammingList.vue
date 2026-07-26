<!--
  PendingProgrammingList.vue — 待编程一览（status=PROGRAMMING 的零件）

  业务背景（2026-07-14 / 2026-07-20）
  ====================
  - 菜单侧：CNC 编程员专属入口；侧栏只挂「待编程一览」（顶层菜单）。
  - 数据侧：调 GET /parts/pending-programming（status=PROGRAMMING 已硬编码于后端）。
  - 两个动作（2026-07-20 移除「文件」按钮 + el-drawer，理由：「df6b4d8 引入的过度设计」）
    * 「详情」 → 跳 /parts/{id}（PartDetail 页内有图纸下载 / G 代码上传 / 设定单上传）
    * 「下发到生产」 → 弹 el-dialog 同时选 PRODUCTION 货架 + 下一道工序，
      调 POST /parts/{id}/release-from-programming（PROGRAMMING → IN_PROCESS）。
      后端要求必须先上传 G_CODE + SETUP_SHEET，否则 400；前端 catch 后 ElMessage.error。
  - 移动端适配（2026-07-21）：
    * 表格用 ResponsiveList 包裹，< md 自动改为卡片流
    * 分页 layout 按 isMobile 切换（手机只保留 prev/pager/next）
    * 下发到生产 el-dialog 用 useDialogSize（手机近全屏）
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

    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="id"
      :empty-text="emptyText"
      :card-class="(row) => (row.is_urgent ? 'rl-card--urgent' : '')"
      stripe
      border
      size="small"
      :row-class-name="rowClassName"
    >
      <el-table-column
        prop="serial_no"
        label="序列号"
        min-width="110"
        fixed="left"
        show-overflow-tooltip align="center">
        <template #default="{ row }">
          <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="drawing_no"
        label="图号"
        min-width="130"
        fixed="left"
        show-overflow-tooltip align="center"/>

      <el-table-column
        prop="name"
        label="名称"
        min-width="200"
        show-overflow-tooltip align="center">
        <template #default="{ row }">
          <router-link :to="`/parts/${row.id}`" class="name-link">
            {{ row.name }}
          </router-link>
        </template>
      </el-table-column>

      <el-table-column prop="quantity" label="数量" min-width="80" align="right" />

      <el-table-column
        prop="planned_delivery_date"
        label="计划交期"
        min-width="120" align="center"/>

      <el-table-column label="客户" min-width="180" show-overflow-tooltip align="center">
        <template #default="{ row }">
          <span v-if="row.customer_path">{{ row.customer_path }}</span>
          <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" min-width="160" fixed="right" align="center">
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
        </template>
      </el-table-column>

      <!-- 手机卡片（2026-07-21 同步 master 的「2 个动作」） -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <router-link :to="`/parts/${row.id}`" class="rl-card-title name-link">
            {{ row.name }}
          </router-link>
        </div>
        <div class="rl-card-sub">
          图号 {{ row.drawing_no || '—' }} · 序列号 {{ row.serial_no || '—' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">数量</span>
            <span class="rl-kv__val">{{ row.quantity }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">计划交期</span>
            <span class="rl-kv__val">{{ row.planned_delivery_date || '—' }}</span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">客户</span>
            <span class="rl-kv__val">
              {{ row.customer_path || row.customer_name || '—' }}
            </span>
          </div>
        </div>
        <div class="rl-card-actions">
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
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 下发到生产 对话框：2026-07-21 改 —— 先选下一道工序，再选目标货架（按 shelf↔process 映射过滤） -->
    <el-dialog
      v-model="releaseDialogVisible"
      title="下发到生产 — 先选下一道工序，再选目标货架"
      :width="releaseDlg.width.value"
      :top="releaseDlg.top.value"
      :fullscreen="releaseDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onReleaseDialogClosed"
    >
      <div v-if="releaseTarget" class="release-summary">
        <div><strong>流水号：</strong>{{ releaseTarget.serial_no || '—' }}</div>
        <div><strong>图号：</strong>{{ releaseTarget.drawing_no }}</div>
        <div><strong>名称：</strong>{{ releaseTarget.name }}</div>
      </div>

      <el-form label-width="110px" style="margin-top: 12px">
        <el-form-item label="下一道工序" required>
          <el-radio-group
            v-model="releaseProcessId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto"
          >
            <el-radio
              v-for="p in filteredInhouseProcesses"
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
            <span v-if="filteredInhouseProcesses.length === 0" class="muted">
              没有 INHOUSE 工序，请先在「设置 → 工序管理」中新增
            </span>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="目标生产货架" required>
          <el-radio-group
            v-model="releaseShelfId"
            :disabled="!releaseProcessId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto"
          >
            <el-radio
              v-for="s in filteredProductionShelves"
              :key="s.id"
              :value="String(s.id)"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-radio>
            <span v-if="filteredProductionShelves.length === 0" class="muted">
              {{
                releaseProcessId
                  ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                  : '没有可用生产货架'
              }}
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
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  RefreshLeft,
  Search,
} from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import {
  listPendingProgramming,
  releaseFromProgramming,
} from '@/api/parts'
import { listShelves } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { PartListItem } from '@/types/parts'
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

const { isMobile } = useBreakpoint()
// 手机上分页收窄为 prev/pager/next，桌面保留完整布局
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

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
const releaseDlg = useDialogSize({ desktopWidth: 560 })
const releaseDialogVisible = ref(false)
const releaseTarget = ref<RowState | null>(null)
const releaseShelfId = ref<string>('')
const releaseProcessId = ref<string>('')
const releaseSubmitting = ref(false)
const productionShelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])
// 2026-07-17：CNC 下发只允许 INHOUSE 工序（外协工序走 send_to_outsource）
const inhouseProcesses = computed(() =>
  processes.value.filter((p) => p.category === 'INHOUSE'),
)

// 2026-07-17：useShelfProcessFilter 双向收窄（CNC 下发对话框）
const {
  filteredShelves: filteredProductionShelves,
  filteredProcesses: filteredInhouseProcesses,
  load: loadReleaseMap,
} = useShelfProcessFilter(
  productionShelves,
  inhouseProcesses,
  computed({
    get: () => releaseShelfId.value || null,
    set: (v) => { releaseShelfId.value = v ?? '' },
  }),
  computed({
    get: () => releaseProcessId.value || null,
    set: (v) => { releaseProcessId.value = v ?? '' },
  }),
)

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
  // 2026-07-17：shelves/processes 加载完后异步拉映射
  void loadReleaseMap()
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

onMounted(() => {
  fetchList()
})
</script>

<style lang="scss" scoped>
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
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;

  @include until(sm) {
    justify-content: center;
  }
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
</style>
