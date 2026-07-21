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

    <!-- 品检打回对话框：选目标生产货架 -->
    <el-dialog
      v-model="failDialogVisible"
      title="品检打回 — 选择目标生产货架"
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
        <el-form-item label="目标生产货架" required>
          <el-radio-group v-model="failShelfId" style="display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto">
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
        <el-alert
          type="info"
          :closable="false"
          title="打回后零件回到「在生产货架上」状态，next_process_id 清空，文员重新下发时再选下一道工序。"
          show-icon
        />
      </el-form>

      <template #footer>
        <el-button @click="failDialogVisible = false">取消</el-button>
        <el-button
          type="warning"
          :loading="failSubmitting"
          :disabled="!failShelfId"
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
import type { PartListItem } from '@/types/parts'

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
const failDlg = useDialogSize({ desktopWidth: 480 })
const failDialogVisible = ref(false)
const failTarget = ref<PartListItem | null>(null)
const failShelfId = ref<string>('')
const failSubmitting = ref(false)
const productionShelves = ref<{ id: string; code: string; name: string; is_active: boolean }[]>([])

async function openFailDialog(row: RowState): Promise<void> {
  failTarget.value = row
  failShelfId.value = ''
  failDialogVisible.value = true
  if (productionShelves.value.length === 0) {
    await loadProductionShelves()
  }
}

async function loadProductionShelves(): Promise<void> {
  try {
    const { listShelves } = await import('@/api/shelves')
    const resp = await listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 })
    productionShelves.value = resp.items.map((s) => ({
      id: String(s.id),
      code: s.code,
      name: s.name,
      is_active: s.is_active,
    }))
  } catch (e) {
    ElMessage.error(`加载生产货架失败：${(e as Error).message}`)
    productionShelves.value = []
  }
}

function onFailDialogClosed(): void {
  failTarget.value = null
  failShelfId.value = ''
}

async function onFailConfirm(): Promise<void> {
  if (!failTarget.value || !failShelfId.value) return
  failSubmitting.value = true
  try {
    await failInspection(failTarget.value.id, failShelfId.value)
    ElMessage.success(
      `零件 ${failTarget.value.serial_no || failTarget.value.drawing_no} 已打回生产货架`,
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
</style>