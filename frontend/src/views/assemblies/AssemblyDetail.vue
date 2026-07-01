<!--
  AssemblyDetail.vue

  /assemblies/:id — 装配件详情页。

  区块：
  - 装配件信息（总图图号、名称、客户、日期、加急、状态、子零件数）
  - 图纸 / 文件列表（FileListCard）
  - 子零件表格（每个零件可点击 → 跳到 /parts/:id）

  数据来源：GET /api/v1/assemblies/:id
-->
<template>
  <div class="assembly-detail" v-loading="loading">
    <el-card v-if="detail" shadow="never" class="info-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            装配件详情
            <el-tag :type="statusTag(detail.assembly.status)" size="small" effect="dark">
              {{ detail.assembly.status }}
            </el-tag>
          </span>
          <div class="card-actions">
            <el-button @click="$router.push('/assemblies')">
              <el-icon><Back /></el-icon>
              <span>返回列表</span>
            </el-button>
            <el-button type="danger" plain @click="onDelete">
              <el-icon><Delete /></el-icon>
              <span>删除装配件</span>
            </el-button>
          </div>
        </div>
      </template>

      <el-descriptions :column="3" border>
        <el-descriptions-item label="总图图号">
          <span class="mono">{{ detail.assembly.drawing_no }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="装配体名称">
          {{ detail.assembly.name }}
        </el-descriptions-item>
        <el-descriptions-item label="客户">
          {{ detail.assembly.customer_path || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="申请人">
          {{ detail.assembly.applicant_name || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="子零件数">
          <el-tag type="info" size="small" effect="plain">
            {{ detail.assembly.child_count }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="加急">
          <el-tag v-if="detail.assembly.is_urgent" type="danger" size="small" effect="dark">加急</el-tag>
          <span v-else class="muted">否</span>
        </el-descriptions-item>
        <el-descriptions-item label="请购日期">
          {{ detail.assembly.request_date }}
        </el-descriptions-item>
        <el-descriptions-item label="计划交期">
          {{ detail.assembly.planned_delivery_date }}
        </el-descriptions-item>
        <el-descriptions-item label="实际送货">
          {{ detail.assembly.actual_delivery_date || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">
          {{ formatDateTime(detail.assembly.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="更新时间">
          {{ formatDateTime(detail.assembly.updated_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="ID">
          <span class="mono">{{ detail.assembly.id }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <FileListCard
      :files="detail?.files ?? []"
      owner-type="assembly"
      :owner-id="assemblyId"
      :show-upload="true"
      :show-delete="true"
      @refresh="fetchData"
    />

    <el-card shadow="never" class="children-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            子零件
            <el-tag v-if="detail" type="info" size="small" effect="plain">
              {{ detail.children.length }} 个
            </el-tag>
          </span>
        </div>
      </template>
      <el-table
        :data="detail?.children ?? []"
        border
        stripe
        size="small"
        :row-class-name="childRowClass"
      >
        <el-table-column type="index" label="#" width="50" />
        <el-table-column label="序列号" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.serial_no" type="success" size="small" effect="dark">
              {{ row.serial_no }}
            </el-tag>
            <span v-else class="muted">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="图号" width="160">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push(`/parts/${row.id}`)">
              {{ row.drawing_no }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="70" align="right" />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="partStatusTag(row.status)" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="计划交期" width="120">
          <template #default="{ row }">{{ row.planned_delivery_date }}</template>
        </el-table-column>
        <el-table-column label="加急" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" size="small" effect="dark">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Delete } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import { getAssembly, softDeleteAssembly } from '@/api/assembly'
import type { AssemblyDetail } from '@/types/assembly'
import type { OrderStatus } from '@/types/parts'

const route = useRoute()
const router = useRouter()

const detail = ref<AssemblyDetail | null>(null)
const loading = ref(false)

// id 是后端 IdStr 序列化的字符串，雪花 ID 完整保留；不再 Number() 转回去
const assemblyId = computed<string>(() => {
  const raw = route.params.id
  return String(Array.isArray(raw) ? raw[0] : raw ?? '')
})

function statusTag(s: string): 'success' | 'info' {
  return s === 'COMPLETED' ? 'success' : 'info'
}

function partStatusTag(s: OrderStatus): 'success' | 'warning' | 'info' | 'danger' | 'primary' {
  switch (s) {
    case 'COMPLETED':
      return 'success'
    case 'CANCELLED':
      return 'info'
    case 'IN_PROCESS':
    case 'INSPECTION':
      return 'warning'
    case 'REPAIRING':
      return 'danger'
    default:
      return 'primary'
  }
}

function childRowClass({ row }: { row: { is_urgent: boolean } }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

function formatDateTime(iso: string): string {
  if (!iso) return '—'
  try {
    return iso.replace('T', ' ').slice(0, 19)
  } catch {
    return iso
  }
}

async function fetchData(): Promise<void> {
  if (!assemblyId.value) return
  loading.value = true
  try {
    detail.value = await getAssembly(assemblyId.value)
  } catch (e) {
    detail.value = null
    ElMessage.error((e as Error).message ?? '加载装配件详情失败')
  } finally {
    loading.value = false
  }
}

async function onDelete(): Promise<void> {
  if (!detail.value) return
  const a = detail.value.assembly
  try {
    await ElMessageBox.confirm(
      `确认删除装配件「${a.name}」？将级联软删 ${a.child_count} 个子零件 + 全部关联文件。`,
      '删除装配件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await softDeleteAssembly(a.id)
    ElMessage.success('已删除')
    router.push('/assemblies')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

watch(() => route.params.id, fetchData)
onMounted(fetchData)
</script>

<style lang="scss" scoped>
.assembly-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.info-card,
.children-card {
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
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.card-actions {
  display: flex;
  gap: 8px;
}
.mono {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: var(--text-regular);
}
.muted {
  color: var(--text-secondary);
}
:deep(.row-urgent) {
  background-color: #fdf6ec !important;
}
</style>