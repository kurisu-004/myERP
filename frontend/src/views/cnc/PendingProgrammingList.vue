<template>
  <div class="pp-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="图号 / 名称">
          <el-input
            v-model="search.keyword"
            clearable
            placeholder="前缀搜索"
            style="width: 220px"
            @keyup.enter="fetchList"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchList">
            <el-icon><Search /></el-icon><span>查询</span>
          </el-button>
          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon><span>重置</span>
          </el-button>
        </el-form-item>
        <el-form-item>
          <span class="text-muted">共 {{ total }} 条待编程</span>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table :data="rows" v-loading="loading" stripe border size="small">
        <el-table-column prop="serial_no" label="流水号" width="120" />
        <el-table-column prop="drawing_no" label="图号" min-width="140" show-overflow-tooltip />
        <el-table-column prop="name" label="品名" min-width="160" show-overflow-tooltip />
        <el-table-column label="客户" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.customer_path || row.customer_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="70" />
        <el-table-column label="加急" width="60">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" size="small">加急</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="计划交期" width="120">
          <template #default="{ row }">
            {{ row.planned_delivery_date || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="ORDER_STATUS_TAG_TYPE[row.status as OrderStatus]" size="small">
              {{ ORDER_STATUS_LABEL[row.status as OrderStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              @click="onOpen(row.id)"
            >
              查看详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, RefreshLeft } from '@element-plus/icons-vue'
import { listPendingProgramming, type PartItem } from '@/api/parts'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  type OrderStatus,
} from '@/types/parts'

const router = useRouter()

const loading = ref(false)
const rows = ref<PartItem[]>([])
const total = ref(0)
const search = reactive({ keyword: '' })

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await listPendingProgramming({
      keyword: search.keyword || undefined,
      limit: 200,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

function onReset(): void {
  search.keyword = ''
  fetchList()
}

function onOpen(id: string): void {
  router.push({ name: 'PartsDetail', params: { id } })
}

onMounted(() => void fetchList())
</script>

<style scoped>
.pp-list { padding: 12px; }
.filter-card { margin-bottom: 12px; }
.text-muted { color: #909399; font-size: 12px; }
</style>
