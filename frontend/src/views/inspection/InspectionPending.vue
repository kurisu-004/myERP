<!--
  InspectionPending.vue — 品检待办一览（INSPECTION 状态的零件）

  - 顶部：图号/名称搜索 + 手动刷新 + 自动刷新（每 10s）+ 共 N 条
  - 每行两个动作：「品检通过」「品检打回」
  - 品检打回 → 弹出 el-dialog 选择目标 PRODUCTION 货架（el-radio-group）
  - 加急行整行红底 #fde2e2（与 PartsList 同款）
-->
<template>
  <div class="inspection-pending">
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
          当前无待品检零件
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

      <el-table-column label="品检货架" width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.shelf_code">品检 {{ row.shelf_code }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button
            link
            type="success"
            size="small"
            :loading="row._passing"
            @click="onPass(row as PartListItem)"
          >品检通过</el-button>
          <el-button
            link
            type="warning"
            size="small"
            @click="openFailDialog(row as PartListItem)"
          >品检打回</el-button>
          <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
        </template>
      </el-table-column>

      <!-- 手机卡片 -->
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
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">品检货架</span>
            <span class="rl-kv__val">
              {{ row.shelf_code ? `品检 ${row.shelf_code}` : '—' }}
            </span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button
            link
            type="success"
            size="small"
            :loading="row._passing"
            @click="onPass(row as PartListItem)"
          >品检通过</el-button>
          <el-button
            link
            type="warning"
            size="small"
            @click="openFailDialog(row as PartListItem)"
          >品检打回</el-button>
          <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
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

    <!-- 品检打回对话框：先选下一道工序，再选目标生产货架（按 shelf↔process 映射过滤） -->
    <el-dialog
      v-model="failDialogVisible"
      title="品检打回 — 选择下一道工序 + 目标生产货架"
      :width="failDlg.width.value"
      :top="failDlg.top.value"
      :fullscreen="failDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onFailDialogClosed"
    >
      <div v-if="failTarget" class="fail-summary">
        <div><strong>流水号：</strong>{{ failTarget.serial_no || '—' }}</div>
        <div><strong>图号：</strong>{{ failTarget.drawing_no }}</div>
        <div><strong>名称：</strong>{{ failTarget.name }}</div>
      </div>

      <el-form label-width="96px" style="margin-top: 12px">
        <el-form-item label="下一道工序" required>
          <el-select
            v-model="failProcessId"
            placeholder="请先选择下一道工序"
            filterable
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="p in filteredProcesses"
              :key="p.id"
              :value="String(p.id)"
              :label="`${p.code} — ${p.name}`"
            >
              {{ p.code }} — {{ p.name }}
              <el-tag v-if="p.category === 'OUTSOURCE'" type="warning" size="small" effect="plain" class="opt-tag">
                外协
              </el-tag>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item label="目标生产货架" required>
          <el-select
            v-model="failShelfId"
            placeholder="先选工序；货架候选按映射过滤"
            filterable
            clearable
            style="width: 100%"
            :disabled="!failProcessId"
          >
            <el-option
              v-for="s in filteredProductionShelves"
              :key="s.id"
              :value="String(s.id)"
              :label="`${s.code} — ${s.name}`"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-option>
            <template #empty>
              <span class="muted">
                {{
                  failProcessId
                    ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                    : '请先选择下一道工序'
                }}
              </span>
            </template>
          </el-select>
        </el-form-item>

        <el-form-item label="品检备注">
          <el-input
            v-model="failNote"
            type="textarea"
            :rows="3"
            :maxlength="500"
            show-word-limit
            placeholder="不合格原因 / 返修要点（写入事件历史，工人领取时可见）"
          />
        </el-form-item>

        <el-alert
          type="info"
          :closable="false"
          title="打回后零件回到「在生产货架上」状态，下一道工序与备注已写入事件历史；工人领取时可在卡片上看到备注。"
          show-icon
        />
      </el-form>

      <template #footer>
        <el-button @click="failDialogVisible = false">取消</el-button>
        <el-button
          type="warning"
          :loading="failSubmitting"
          :disabled="!failProcessId || !failShelfId"
          @click="onFailConfirm"
        >确认打回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshLeft, Search } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import { failInspection, listParts, passInspection } from '@/api/parts'
import type { ListPartsParams } from '@/api/parts'
import { listShelves } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { PartListItem } from '@/types/parts'
import type { Shelf } from '@/types/shelf'
import type { Process } from '@/types/process'

// ============ 状态 ============
interface RowState extends PartListItem {
  _passing?: boolean
}
const items = ref<RowState[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)

const search = reactive({ keyword: '' })

const emptyText = computed(() => errorMsg.value ?? '暂无待品检零件')

const { isMobile } = useBreakpoint()
// 手机上分页收窄为 prev/pager/next，桌面保留完整布局
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

function rowClassName({ row }: { row: PartListItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

function buildParams(): ListPartsParams {
  return {
    statuses: ['INSPECTION'],
    keyword: search.keyword.trim() || undefined,
    sort_by: 'PLANNED_DELIVERY_DATE',
    sort_dir: 'ASC',
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

// ============ 品检通过 ============
async function onPass(row: RowState): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认零件「${row.name}」(${row.serial_no || row.drawing_no})品检合格，进入待送货状态？`,
      '品检通过',
      { type: 'success', confirmButtonText: '确认通过', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  row._passing = true
  try {
    await passInspection(row.id)
    ElMessage.success(`零件 ${row.serial_no || row.drawing_no} 品检通过`)
    await fetchList()
  } catch (e) {
    ElMessage.error(`品检通过失败：${(e as Error).message}`)
  } finally {
    row._passing = false
  }
}

// ============ 品检打回对话框 ============
// 2026-07-21 改：先选下一道工序，再选目标生产货架（按 shelf↔process 映射过滤）。
// 同时支持可选「品检备注」，写入 t_part_event.note，事件历史与工人领取卡片均可见。
const failDlg = useDialogSize({ desktopWidth: 520 })
const failDialogVisible = ref(false)
const failTarget = ref<PartListItem | null>(null)
const failProcessId = ref<string>('')
const failShelfId = ref<string>('')
const failNote = ref<string>('')
const failSubmitting = ref(false)
const productionShelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])
// 品检打回默认走 INHOUSE 工序（外协工序走 send_to_outsource 路径）；
// 不强制过滤 category，避免业务上「品检后直接外协返修」分支被锁死。
const {
  filteredShelves: filteredProductionShelves,
  filteredProcesses,
  load: loadShelfProcessMap,
} = useShelfProcessFilter(
  productionShelves,
  processes,
  computed({
    get: () => failShelfId.value || null,
    set: (v) => { failShelfId.value = v ?? '' },
  }),
  computed({
    get: () => failProcessId.value || null,
    set: (v) => { failProcessId.value = v ?? '' },
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

async function openFailDialog(row: RowState): Promise<void> {
  failTarget.value = row
  failProcessId.value = ''
  failShelfId.value = ''
  failNote.value = ''
  failDialogVisible.value = true
  await Promise.all([
    productionShelves.value.length === 0 ? loadProductionShelves() : Promise.resolve(),
    processes.value.length === 0 ? loadProcesses() : Promise.resolve(),
  ])
  // shelves/processes 加载完后异步拉映射；映射未到位前 filteredXxx 走兜底全量
  void loadShelfProcessMap()
}

function onFailDialogClosed(): void {
  failTarget.value = null
  failProcessId.value = ''
  failShelfId.value = ''
  failNote.value = ''
}

async function onFailConfirm(): Promise<void> {
  if (!failTarget.value || !failProcessId.value || !failShelfId.value) return
  const row = failTarget.value
  const shelfCode =
    productionShelves.value.find((s) => String(s.id) === failShelfId.value)?.code ?? ''
  const processCode =
    processes.value.find((p) => String(p.id) === failProcessId.value)?.code ?? ''
  try {
    await ElMessageBox.confirm(
      `确认打回「${row.name}」（${row.serial_no || row.drawing_no}）到生产货架 ${shelfCode}，下一道工序 ${processCode}？`,
      '品检打回',
      { type: 'warning', confirmButtonText: '确认打回', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  failSubmitting.value = true
  try {
    await failInspection(row.id, {
      shelf_id: failShelfId.value,
      next_process_id: failProcessId.value,
      note: failNote.value.trim() || null,
    })
    ElMessage.success(
      `零件 ${row.serial_no || row.drawing_no} 已打回生产货架 ${shelfCode}`,
    )
    failDialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error(`品检打回失败：${(e as Error).message}`)
  } finally {
    failSubmitting.value = false
  }
}

onMounted(() => {
  fetchList()
})
</script>

<style lang="scss" scoped>
.inspection-pending {
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
.fail-summary {
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
  padding: 10px 14px;
  line-height: 1.8;
  font-size: 13px;
}
.opt-tag {
  margin-left: 6px;
}
</style>