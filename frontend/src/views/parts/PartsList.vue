<!--
  PartsList.vue

  零件一览表。字段与后端 PartOut 严格对齐：
    id / serial_no / drawing_no / name / quantity
    / planned_delivery_date / actual_delivery_date
    / is_urgent / status / customer_name / parent_customer_name / customer_path

  - 列表/分页走 GET /api/v1/parts，limit/offset 服务端分页。
  - 搜索条件：drawing_no_like / name_like / status / is_urgent（与后端 PartListQuery 一一对应）。
  - 排序仅支持 planned_delivery_date（与后端 PartSortKey 对齐）。
  - 删除走 POST /api/v1/parts/{id}/soft-delete。
  - 编辑尚未实装（路由 /parts/{id}/edit 不存在），按钮暂时给 ElMessage 提示。
-->

<template>
  <div class="parts-list">
    <el-card shadow="never" class="filter-card">
      <el-form :model="search" inline label-width="80px" @submit.prevent="onSearch">
        <el-form-item label="图号">
          <el-input
            v-model="search.drawingNo"
            placeholder="如：LT39822"
            clearable
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="名称">
          <el-input
            v-model="search.name"
            placeholder="如：塞规"
            clearable
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="search.status"
            placeholder="全部"
            clearable
            style="width: 150px"
          >
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="加急">
          <el-select
            v-model="search.isUrgent"
            placeholder="全部"
            clearable
            style="width: 110px"
          >
            <el-option label="是" value="true" />
            <el-option label="否" value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="onSearch">
            <el-icon><Search /></el-icon>
            <span>查询</span>
          </el-button>
          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon>
            <span>重置</span>
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <div class="sheet-wrapper">
      <el-table
        :data="items"
        v-loading="loading"
        stripe
        border
        style="width: 100%"
        size="small"
        :default-sort="{ prop: 'planned_delivery_date', order: sortDir === 'ASC' ? 'ascending' : 'descending' }"
        @sort-change="onSortChange"
        :empty-text="emptyText"
      >
        <!-- <el-table-column type="index" label="#" width="48" fixed="left" /> -->
        <el-table-column prop="serial_no" label="序列号" width="110" fixed="left" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'muted': !row.serial_no }">{{ row.serial_no || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="drawing_no" label="图号" width="130" fixed="left" show-overflow-tooltip />
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <router-link :to="`/parts/${row.id}`" class="name-link">
              {{ row.name }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="80" align="right" sortable="custom" />
        <el-table-column prop="planned_delivery_date" label="计划交期" width="120" sortable="custom" />

        <el-table-column prop="is_urgent" label="加急" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" effect="dark" size="small">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="customer_path" label="客户" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.customer_path">{{ row.customer_path }}</span>
            <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="装配" width="80" align="center">
          <template #default="{ row }">
            <el-tag
              v-if="row.assembly_id != null"
              type="primary"
              size="small"
              effect="plain"
              class="assembly-link"
              @click.stop="$router.push(`/parts/${row.id}/assembly`)"
            >
              <el-icon><Connection /></el-icon>
              <span>装配件</span>
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="actual_delivery_date" label="实际送货" width="120">
          <template #default="{ row }">
            <span :class="{ 'muted': !row.actual_delivery_date }">{{ row.actual_delivery_date || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEdit(row as PartItem)">编辑</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as PartItem)">删除</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>
    </div>

    <div class="pagination">
      <span class="total-text">共 {{ total }} 条记录</span>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="sizes, prev, pager, next, jumper"
        background
        size="small"
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Connection } from '@element-plus/icons-vue'
import {
  listParts,
  softDeletePart,
  type ListPartsParams,
  type PartItem,
} from '@/api/parts'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  type OrderStatus,
  type PartSortKey,
  type SortDir,
} from '@/types/parts'

// ============ 搜索条件（与后端 PartListQuery 对齐）============
const initialSearch = () => ({
  drawingNo: '',
  name: '',
  status: '' as '' | OrderStatus,
  isUrgent: '' as '' | 'true' | 'false',
})
const search = reactive(initialSearch())

const statusOptions: { value: OrderStatus; label: string }[] = (
  Object.keys(ORDER_STATUS_LABEL) as OrderStatus[]
).map((v) => ({ value: v, label: ORDER_STATUS_LABEL[v] }))

// ============ 表格数据 / 分页 / 排序 ============
const items = ref<PartItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)
const sortBy = ref<PartSortKey>('PLANNED_DELIVERY_DATE')
const sortDir = ref<SortDir>('ASC')

const emptyText = computed(() => errorMsg.value ?? '暂无符合条件的零件')

function statusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}

function buildParams(): ListPartsParams {
  return {
    drawing_no_like: search.drawingNo.trim() || undefined,
    name_like: search.name.trim() || undefined,
    status: search.status || undefined,
    is_urgent: search.isUrgent === '' ? undefined : search.isUrgent === 'true',
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
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
    ElMessage.error(errorMsg.value)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void fetchList()
})

// ============ 搜索 / 重置 / 排序 / 分页 ============
const onSearch = (): void => {
  page.value = 1
  void fetchList()
}
const onReset = (): void => {
  Object.assign(search, initialSearch())
  page.value = 1
  void fetchList()
}
const onSortChange = ({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void => {
  // 暂时只把 planned_delivery_date 映射成后端 PartSortKey
  if (prop === 'planned_delivery_date') {
    sortBy.value = 'PLANNED_DELIVERY_DATE'
  } else if (prop === 'quantity') {
    // quantity 后端无对应 sort_by，按点击顺序切换升降序表达，不传后端
  }
  if (order === 'ascending') sortDir.value = 'ASC'
  else if (order === 'descending') sortDir.value = 'DESC'
  void fetchList()
}
const onPageSizeChange = (size: number): void => {
  pageSize.value = size
  page.value = 1
  void fetchList()
}

// ============ 行操作 ============
const onEdit = (row: PartItem): void => {
  ElMessage.info(`编辑 #${row.id}（${row.drawing_no}）— 待实装`)
}
const onDelete = (row: PartItem): void => {
  ElMessageBox.confirm(
    `确认删除零件「${row.name}」（图号：${row.drawing_no}）？此操作将软删。`,
    '提示',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
    .then(async () => {
      try {
        await softDeletePart(row.id)
        ElMessage.success('删除成功')
        // 当前页删完后退到上一页；否则保持当前页
        if (items.value.length === 1 && page.value > 1) {
          page.value -= 1
        }
        void fetchList()
      } catch (e) {
        ElMessage.error((e as Error).message ?? '删除失败')
      }
    })
    .catch(() => undefined)
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
    padding-bottom: 0;
  }
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
  justify-content: space-between;
  align-items: center;
  padding: 0 4px;
}

.total-text {
  color: var(--text-secondary);
  font-size: 13px;
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

.assembly-link {
  cursor: pointer;
  user-select: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  &:hover {
    background: var(--primary-color) !important;
    color: #fff !important;
  }
}
</style>