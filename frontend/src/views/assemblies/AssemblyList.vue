<!--
  AssemblyList.vue

  /assemblies — 装配件列表（与 /parts 并列）。

  表格列：总图图号 / 名称 / 客户 / 子零件数 / 计划交期 / 加急 / 状态 / 操作。
  支持按客户、状态、加急、图号、名称过滤 + 分页。
-->
<template>
  <div class="assembly-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline :model="filter" @submit.prevent="onSearch">
        <el-form-item label="客户">
          <el-cascader
            v-model="filter.customer_id"
            :options="customerTree"
            :props="{
              value: 'id',
              label: 'name',
              children: 'children',
              checkStrictly: true,
              emitPath: false,
            }"
            placeholder="全部"
            clearable
            style="width: 220px"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="filter.status"
            placeholder="全部"
            clearable
            style="width: 140px"
          >
            <el-option label="PENDING" value="PENDING" />
            <el-option label="COMPLETED" value="COMPLETED" />
          </el-select>
        </el-form-item>
        <el-form-item label="加急">
          <el-select
            v-model="filter.is_urgent"
            placeholder="全部"
            clearable
            style="width: 100px"
          >
            <el-option label="加急" :value="true" />
            <el-option label="非加急" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="图号">
          <el-input v-model="filter.drawing_no_like" placeholder="模糊匹配" clearable />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="filter.name_like" placeholder="模糊匹配" clearable />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">
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

    <el-card shadow="never" class="table-card" v-loading="loading">
      <template #header>
        <div class="card-header">
          <span class="card-title">装配件列表</span>
          <div class="card-actions">
            <el-button type="primary" @click="$router.push('/assemblies/new')">
              <el-icon><Plus /></el-icon>
              <span>新建装配件</span>
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="items"
        border
        stripe
        size="small"
        :row-class-name="rowClassName"
        @row-click="onRowClick"
      >
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="drawing_no" label="总图图号" width="160" show-overflow-tooltip />
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="客户" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.customer_path || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="子零件" width="80" align="center">
          <template #default="{ row }">
            <el-tag type="info" size="small" effect="plain">
              {{ row.child_count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="planned_delivery_date" label="计划交期" width="120" />
        <el-table-column label="加急" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" size="small" effect="dark">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click.stop="$router.push(`/assemblies/${row.id}`)">
              查看
            </el-button>
            <el-button link type="danger" size="small" @click.stop="onDelete(row as AssemblyItem)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="sizes, prev, pager, next, jumper, total"
        background
        style="margin-top: 12px; justify-content: flex-end"
        @current-change="fetchData"
        @size-change="onSizeChange"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElMessage,
  ElMessageBox,
} from 'element-plus'
import { Plus, RefreshLeft, Search } from '@element-plus/icons-vue'
import { listAssemblies, softDeleteAssembly } from '@/api/assembly'
import { listCustomers, type Customer } from '@/api/customer'
import type { AssemblyItem, AssemblyListQuery } from '@/types/assembly'

const router = useRouter()

// ============ 客户级联（仅过滤用） ============
const customers = ref<Customer[]>([])
const customerTree = computed(() => {
  const roots = customers.value.filter((c) => c.parent_id === null)
  return roots.map((r) => ({
    id: r.id,
    name: r.name,
    children: customers.value
      .filter((c) => c.parent_id === r.id)
      .map((c) => ({ id: c.id, name: c.name })),
  }))
})

// ============ 过滤 + 分页 ============
const filter = reactive<AssemblyListQuery>({})
const items = ref<AssemblyItem[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)

function statusTag(s: string): 'success' | 'info' | 'warning' | 'danger' {
  if (s === 'COMPLETED') return 'success'
  return 'info'
}

function rowClassName({ row }: { row: AssemblyItem }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

async function fetchData(): Promise<void> {
  loading.value = true
  try {
    const res = await listAssemblies({
      ...filter,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    items.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载装配件列表失败')
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  page.value = 1
  void fetchData()
}

function onReset(): void {
  Object.assign(filter, {})
  page.value = 1
  void fetchData()
}

function onSizeChange(s: number): void {
  pageSize.value = s
  page.value = 1
  void fetchData()
}

function onRowClick(row: AssemblyItem): void {
  router.push(`/assemblies/${row.id}`)
}

async function onDelete(row: AssemblyItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认软删除装配件「${row.name}」？将级联软删 ${row.child_count} 个子零件 + 全部关联文件。`,
      '删除装配件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await softDeleteAssembly(row.id)
    ElMessage.success('已删除')
    if (items.value.length === 1 && page.value > 1) page.value--
    void fetchData()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

onMounted(async () => {
  try {
    customers.value = await listCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '客户列表加载失败')
  }
  void fetchData()
})
</script>

<style lang="scss" scoped>
.assembly-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.filter-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.table-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
}
.muted {
  color: var(--text-secondary);
}
:deep(.row-urgent) {
  background-color: #fdf6ec !important;
}
</style>