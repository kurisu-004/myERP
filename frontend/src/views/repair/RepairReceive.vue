<script setup lang="ts">
/**
 * 返修接收 (Repair Receive) 主页面（PR-M 2026-08-04）。
 *
 * 双 Tab:
 * - 「已送货」(DELIVERED)：操作栏点「返修」→ start_repair
 * - 「返修中」(REPAIRING)：操作栏点「完成返修」→ complete_repair（弹 dialog 选工序/货架）
 *
 * 扫描：useBarcodeScanner 全局监听；命中弹 dialog；未命中复用报工台 findPartBySerialAndPrompt。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Tools } from '@element-plus/icons-vue'
import {
  listRepairBatches,
  listRepairingBatches,
  startPartRepair,
  completePartRepair,
  getPartBySerial,
} from '@/api/parts'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { findAllByCode, findPartBySerialAndPrompt } from '@/utils/scanHelpers'
import type { PartItem } from '@/api/parts'
import RepairStartDialog from './RepairStartDialog.vue'
import RepairDispatchDialog from './RepairDispatchDialog.vue'

const { onScan } = useBarcodeScanner()

type TabKey = 'delivered' | 'repairing'
const activeTab = ref<TabKey>('delivered')

// —— 列表状态 ——
const rows = ref<PartItem[]>([])
const total = ref(0)
const loading = ref(false)
const limit = 50
const offset = ref(0)

const keyword = ref('')
const customerId = ref('')

// —— Dialog 状态 ——
const startDialog = ref<{ open: boolean; target: PartItem | null }>({
  open: false, target: null,
})
const dispatchDialog = ref<{ open: boolean; target: PartItem | null }>({
  open: false, target: null,
})

// —— 行 loading 状态 ——
const rowPending = ref<Record<string, boolean>>({})

// —— 列表加载 ——
async function loadList(): Promise<void> {
  loading.value = true
  try {
    const params = {
      keyword: keyword.value || undefined,
      customer_id: customerId.value || undefined,
      limit,
      offset: offset.value,
    }
    const result = activeTab.value === 'delivered'
      ? await listRepairBatches(params)
      : await listRepairingBatches(params)
    rows.value = result.items
    total.value = result.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '列表加载失败')
  } finally {
    loading.value = false
  }
}

function reloadCurrent(): void {
  offset.value = 0
  void loadList()
}

async function switchTab(tab: TabKey): Promise<void> {
  activeTab.value = tab
  reloadCurrent()
}

function onTabChange(tab: string | number): void {
  void switchTab(tab as TabKey)
}

// —— 操作按钮 ——
function onClickStartRepair(row: PartItem): void {
  startDialog.value = { open: true, target: row }
}

function onClickCompleteRepair(row: PartItem): void {
  dispatchDialog.value = { open: true, target: row }
}

async function onStartDialogConfirm(): Promise<void> {
  await loadList()
}
async function onDispatchDialogConfirm(): Promise<void> {
  await loadList()
}

// —— 扫描处理 ——
async function handleScan(code: string): Promise<void> {
  // 1) 在当前列表里查
  const matches = rows.value.filter(
    (r) =>
      (r.serial_no === code || r.drawing_no === code) &&
      ((activeTab.value === 'delivered' && r.status === 'DELIVERED')
        || (activeTab.value === 'repairing' && r.status === 'REPAIRING')),
  )
  const found = findAllByCode(rows.value, code).filter(
    (r) =>
      (activeTab.value === 'delivered' && r.status === 'DELIVERED')
      || (activeTab.value === 'repairing' && r.status === 'REPAIRING'),
  )
  if (found.length === 1) {
    if (activeTab.value === 'delivered') onClickStartRepair(found[0])
    else onClickCompleteRepair(found[0])
    return
  }
  if (found.length > 1) {
    // v1: 简单按第一条（多批次选择可后续加 BatchPickerDialog）
    if (activeTab.value === 'delivered') onClickStartRepair(found[0])
    else onClickCompleteRepair(found[0])
    return
  }
  // 2) 未命中 → 报工台兜底
  await findPartBySerialAndPrompt(code)
}

// —— 生命周期 ——
let unsubScan: (() => void) | null = null

function rowClassNameHandler(opts: { row: PartItem }): string {
  return opts.row.is_urgent ? 'row-urgent' : ''
}
onMounted(async () => {
  await loadList()
  unsubScan = onScan((code) => void handleScan(code))
})
onBeforeUnmount(() => {
  if (unsubScan) unsubScan()
})

// — 导出供调试 ——
defineExpose({
  activeTab,
  rows,
  total,
  loading,
  reloadCurrent,
})
</script>

<template>
  <div class="repair-receive">
    <el-tabs
      v-model="activeTab"
      @tab-change="onTabChange"
    >
      <el-tab-pane label="已送货" name="delivered" />
      <el-tab-pane label="返修中" name="repairing" />
    </el-tabs>

    <!-- 搜索行 -->
    <div class="filter-row">
      <el-input
        v-model="keyword"
        placeholder="图号 / 名称 / 序列号"
        clearable
        style="width: 240px"
        @keyup.enter="reloadCurrent"
      />
      <el-input
        v-model="customerId"
        placeholder="客户 id（可选）"
        clearable
        style="width: 200px"
        @keyup.enter="reloadCurrent"
      />
      <el-button type="primary" @click="reloadCurrent">查询</el-button>
    </div>

    <!-- 列表 -->
    <el-table
      v-loading="loading"
      :data="rows"
      stripe
      border
      style="margin-top: 16px"
      :row-class-name="rowClassNameHandler"
    >
      <el-table-column prop="serial_no" label="流水号" width="100" />
      <el-table-column prop="drawing_no" label="图号" width="160" />
      <el-table-column prop="name" label="名称" min-width="200" />
      <el-table-column label="数量" width="80" prop="quantity" />
      <el-table-column label="批次" width="100">
        <template #default="{ row }">
          <span v-if="row.batch_label">{{ row.batch_label }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          {{ row.status }}
        </template>
      </el-table-column>
      <el-table-column label="返修" width="80">
        <template #default="{ row }">
          <el-tag
            v-if="row.has_been_repaired"
            type="warning"
            size="small"
            effect="dark"
          >
            返修
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="当前位置" min-width="160">
        <template #default="{ row }">
          {{ row.current_holder_display || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="activeTab === 'delivered'"
            type="warning"
            size="small"
            :loading="rowPending[row.id] ?? false"
            @click="onClickStartRepair(row as PartItem)"
          >
            <el-icon><Tools /></el-icon>
            <span>返修</span>
          </el-button>
          <el-button
            v-else
            type="success"
            size="small"
            :loading="rowPending[row.id] ?? false"
            @click="onClickCompleteRepair(row as PartItem)"
          >
            完成返修
          </el-button>
        </template>
      </el-table-column>

      <template #empty>
        <el-empty :description="activeTab === 'delivered' ? '暂无已送货件' : '暂无返修中件'" />
      </template>
    </el-table>

    <!-- 分页：预留简单版；Element Plus 风格 -->
    <div class="pagination-row">
      <span class="muted">共 {{ total }} 条</span>
    </div>

    <RepairStartDialog
      v-model="startDialog.open"
      :target="startDialog.target"
      @confirm="onStartDialogConfirm"
    />
    <RepairDispatchDialog
      v-model="dispatchDialog.open"
      :target="dispatchDialog.target"
      @confirm="onDispatchDialogConfirm"
    />
  </div>
</template>

<style scoped>
.repair-receive {
  padding: 16px;
}
.filter-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 12px 0;
}
.muted {
  color: #909399;
}
.pagination-row {
  margin-top: 12px;
  font-size: 14px;
  color: #606266;
}
/* 加急行红底，与 Dashboard 一致 */
:deep(.row-urgent) {
  background: #fde2e2 !important;
}
</style>
