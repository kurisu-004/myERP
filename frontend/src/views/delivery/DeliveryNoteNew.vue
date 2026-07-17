<!--
  DeliveryNoteNew.vue — 文员生成送货单（PR-F 2026-07-17 重设计）

  - 默认拉 status=READY_TO_SHIP 的零件列表（按 planned_delivery_date 升序）
  - 支持客户筛选（el-cascader，与 PartsList 同款 useCustomerTree）
  - 加急筛选（仅加急）
  - el-table 多选 + 顶部「全选/反选/已选 N 件」
  - 列：选择 | 流水号 | 图号 | 名称 | 订单号 | 系统交期 | 数量 | 分厂/客户 | 计划交期 | 备注
  - 加急行整行红底
  - 底部「生成送货单」按钮 → 调 generateDeliveryNote 拿 Blob → 触发浏览器下载
  - 后端按零件所属 L1 root 的 serial_prefix 自动分发模板（F=法拉 / L=路达）
-->
<template>
  <div class="delivery-note-new">
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="search.keyword"
          placeholder="图号 / 名称（前缀搜索）"
          clearable
          style="width: 240px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-cascader
          v-model="customerPath"
          :options="customerTree"
          :props="cascaderProps"
          placeholder="客户（默认全部）"
          clearable
          style="width: 260px"
          @change="onSearch"
        />

        <el-checkbox v-model="search.onlyUrgent" @change="onSearch">
          仅加急
        </el-checkbox>

        <el-button @click="onReset">
          <el-icon><RefreshLeft /></el-icon>
          <span>重置</span>
        </el-button>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条待送货</span>
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
        row-key="id"
        :empty-text="emptyText"
        :row-class-name="rowClassName"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="55" />

        <el-table-column prop="serial_no" label="流水号" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="drawing_no" label="图号" width="130" show-overflow-tooltip />

        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <router-link :to="`/parts/${row.id}`" class="name-link">{{ row.name }}</router-link>
          </template>
        </el-table-column>

        <el-table-column prop="order_no" label="订单号" width="130" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.order_no }">{{ row.order_no || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="system_delivery_date" label="系统交期" width="110">
          <template #default="{ row }">
            <span :class="{ muted: !row.system_delivery_date }">{{ row.system_delivery_date || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="quantity" label="数量" width="70" align="right" />

        <el-table-column label="分厂/客户" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.customer_path">{{ row.customer_path }}</span>
            <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column prop="planned_delivery_date" label="计划交期" width="110" />

        <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.note }">{{ row.note || '—' }}</span>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>
    </div>

    <div class="bottom-bar">
      <div class="bar-info">
        <span>已选 <strong>{{ selectedIds.length }}</strong> 件</span>
        <el-button link size="small" @click="onSelectAll">全选当前页</el-button>
        <el-button link size="small" @click="onClearSelection">清空选择</el-button>
        <span v-if="selectedIds.length > 0" class="customer-hint">
          · 客户：<strong>{{ inferredCustomerPath || '混合' }}</strong>
        </span>
      </div>
      <el-button
        type="primary"
        :loading="generating"
        :disabled="selectedIds.length === 0"
        @click="onGenerate"
      >
        <el-icon><Document /></el-icon>
        <span>生成送货单</span>
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, RefreshLeft, Search } from '@element-plus/icons-vue'
import { listParts, type ListPartsParams } from '@/api/parts'
import type { PartListItem } from '@/types/parts'
import { generateDeliveryNote } from '@/api/deliveryNote'
import { useCustomerTree } from '@/composables/useCustomerTree'

const items = ref<PartListItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const generating = ref(false)

const search = reactive({ keyword: '', onlyUrgent: false })
const customerPath = ref<string[]>([])
const { tree: customerTree } = useCustomerTree()

const selectedRows = ref<PartListItem[]>([])
const selectedIds = computed(() => selectedRows.value.map((r) => r.id))

const cascaderProps = {
  checkStrictly: true,
  emitPath: true,
  value: 'id',
  label: 'name',
  children: 'children',
}

const emptyText = computed(
  () => errorMsg.value ?? '暂无待送货零件（status=READY_TO_SHIP）',
)

/** 从选中行推断的客户路径（仅当全部同属一个 L1 root 时有值） */
const inferredCustomerPath = computed(() => {
  if (selectedRows.value.length === 0) return ''
  const paths = new Set(
    selectedRows.value
      .map((r) => r.customer_path ?? r.customer_name ?? '')
      .filter(Boolean),
  )
  if (paths.size === 1) return Array.from(paths)[0]
  return ''
})

function rowClassName({ row }: { row: PartListItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

function buildParams(): ListPartsParams {
  const params: ListPartsParams = {
    statuses: ['READY_TO_SHIP'],
    keyword: search.keyword.trim() || undefined,
    is_urgent: search.onlyUrgent || undefined,
    sort_by: 'PLANNED_DELIVERY_DATE',
    sort_dir: 'ASC',
    limit: 200,
    offset: 0,
  }
  // cascader 选了叶子客户 → 传 customer_id
  const leafId = customerPath.value[customerPath.value.length - 1]
  if (leafId) {
    params.customer_id = leafId
  }
  return params
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const resp = await listParts(buildParams())
    items.value = resp.items
    total.value = resp.total
    const validIds = new Set(items.value.map((r) => r.id))
    selectedRows.value = selectedRows.value.filter((r) => validIds.has(r.id))
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  fetchList()
}

function onReset(): void {
  search.keyword = ''
  search.onlyUrgent = false
  customerPath.value = []
  fetchList()
}

function onSelectionChange(rows: PartListItem[]): void {
  selectedRows.value = rows
}

function onSelectAll(): void {
  selectedRows.value = [...items.value]
}

function onClearSelection(): void {
  selectedRows.value = []
}

async function onGenerate(): Promise<void> {
  if (selectedIds.value.length === 0) return
  const customer = inferredCustomerPath.value || '混合'
  try {
    await ElMessageBox.confirm(
      `将为客户「${customer}」生成送货单 Excel（${selectedIds.value.length} 件），确认继续？`,
      '生成送货单',
      { type: 'info', confirmButtonText: '确认生成', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  generating.value = true
  try {
    const blob = await generateDeliveryNote(selectedIds.value)
    _downloadBlob(blob, _todayFilename())
    ElMessage.success('送货单已生成，开始下载')
  } catch (e) {
    ElMessage.error(`生成失败：${(e as Error).message}`)
  } finally {
    generating.value = false
  }
}

function _todayFilename(): string {
  const d = new Date()
  const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `delivery_note_${ymd}.xlsx`
}

function _downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

onMounted(() => {
  // useCustomerTree composable 内部已 onMounted 自动 load 客户列表
  fetchList()
})
</script>

<style scoped>
.delivery-note-new {
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
.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding: 12px 16px;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.bar-info {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 13px;
}
.bar-info strong {
  color: var(--el-color-primary);
  margin: 0 4px;
}
.customer-hint strong {
  color: var(--el-color-warning);
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
/* 加急行红底（与 PartsList / InspectionPending 同款） */
:deep(.row-urgent) {
  background-color: #fde2e2 !important;
}
:deep(.row-urgent:hover > td) {
  background-color: #fdd2d2 !important;
}
</style>