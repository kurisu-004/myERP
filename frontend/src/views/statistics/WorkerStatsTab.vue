<!--
  WorkerStatsTab.vue

  Tab 2 - 工人报工总览。

  工具行：
    - 工种筛选（全部 / 各工种 / 未分配）
    - 含已停用 switch

  图：当前工种内所有工人的「领取次数」水平条形图（y 轴姓名，x 轴 pickup_count）；
     选「全部」时按 pickup_count 降序的前 10 名。

  表：默认按 pickup_count desc；详情按钮 emit 'select-worker'。

  数据：后端一次性返回所有未软删工人（含 is_active=false）；前端按工种 / is_active
  客户端筛选。
-->

<template>
  <div v-loading="loading" class="worker-stats-tab" element-loading-text="加载中">
    <!-- 工具行 -->
    <el-card shadow="never" class="filter-card">
      <el-form inline @submit.prevent>
        <el-form-item label="工种">
          <el-select
            v-model="workTypeFilter"
            placeholder="全部"
            clearable
            style="width: 180px"
            @change="onWorkTypeChange"
          >
            <el-option label="全部工种" value="__all__" />
            <el-option label="未分配工种" value="__none__" />
            <el-option
              v-for="wt in workTypeOptions"
              :key="wt.id"
              :label="`${wt.code} / ${wt.name}`"
              :value="wt.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="含已停用">
          <el-switch v-model="includeInactive" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 图 -->
    <el-card shadow="never" class="chart-card">
      <template #header>
        <span class="chart-title">
          {{ chartTitle }}
        </span>
      </template>
      <EChart :option="barOption" height="360px" />
    </el-card>

    <!-- 表 -->
    <el-card shadow="never">
      <el-table
        :data="sortedRows"
        row-key="worker_id"
        stripe
        border
        size="default"
        empty-text="暂无工人数据"
      >
        <el-table-column prop="worker_name" label="工人" min-width="120" align="center">
          <template #default="{ row }">
            <span>{{ row.worker_name }}</span>
            <el-tag
              v-if="!row.is_active"
              type="info"
              size="small"
              effect="plain"
              style="margin-left: 6px"
            >已停用</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="badge_code" label="工牌" min-width="140" align="center" />
        <el-table-column prop="work_type_name" label="工种" min-width="140" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.work_type_name" type="primary" size="small" effect="plain">
              {{ row.work_type_name }}
            </el-tag>
            <span v-else style="color: #c0c4cc">未分配</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="participated_part_count" label="参与工单数" min-width="110" align="center"
          sortable
        />
        <el-table-column
          prop="pickup_count" label="领取次数" min-width="100" align="center"
          sortable
        />
        <el-table-column
          prop="pickup_quantity" label="领取件数" min-width="100" align="center"
          sortable
        />
        <el-table-column label="贡献度" min-width="200" align="center">
          <template #default="{ row }">
            <el-progress
              v-if="row.contribution_pct !== null"
              :percentage="Number(row.contribution_pct.toFixed(1))"
              :stroke-width="10"
            />
            <span v-else style="color: #c0c4cc">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="100" fixed="right" align="center">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              @click="onSelectWorker(row.worker_id)"
            >详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import EChart from '@/components/EChart.vue'
import { fetchWorkerStats } from '@/api/statistics'
import { listWorkTypes } from '@/api/workType'
import type { WorkerStatsItem } from '@/types/statistics'
import type { WorkType } from '@/types/workType'

interface Props {
  dateFrom: string
  dateTo: string
}
const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select-worker', workerId: string): void
}>()

const loading = ref(false)
const rows = ref<WorkerStatsItem[]>([])

// 工种筛选：
//   '__all__' = 全部
//   '__none__' = 未分配工种
//   string(雪花 ID) = 指定工种
const workTypeFilter = ref<'__all__' | '__none__' | string>('__all__')
const includeInactive = ref(false)

const workTypes = ref<WorkType[]>([])
const workTypeOptions = computed(() => workTypes.value)

// 前端筛过的工人列表
const filteredRows = computed<WorkerStatsItem[]>(() => {
  const wt = workTypeFilter.value
  return rows.value.filter((r) => {
    if (!includeInactive.value && !r.is_active) return false
    if (wt === '__all__') return true
    if (wt === '__none__') return r.work_type_id === null
    return r.work_type_id === wt
  })
})

// 默认 sort：领取次数降序。
const sortedRows = computed<WorkerStatsItem[]>(() => {
  return [...filteredRows.value].sort((a, b) => b.pickup_count - a.pickup_count)
})

async function reload(): Promise<void> {
  if (!props.dateFrom || !props.dateTo) return
  loading.value = true
  try {
    const res = await fetchWorkerStats({
      date_from: props.dateFrom,
      date_to: props.dateTo,
    })
    rows.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工人列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await reload()
  // 工种下拉只用于筛选，本地缓存。
  try {
    const res = await listWorkTypes({ limit: 200 })
    workTypes.value = res.items
  } catch {
    // 工种筛选下拉缺失不阻塞主流程。
  }
})
watch(() => [props.dateFrom, props.dateTo], reload)

function onSelectWorker(workerId: string): void {
  emit('select-worker', workerId)
}

function onWorkTypeChange(): void {
  // 仅触发 computed；无需主动 reload。
}

// ============== ECharts options ==============

const chartTitle = computed<string>(() => {
  const wt = workTypeFilter.value
  if (wt === '__all__') return '领取次数 Top 10'
  if (wt === '__none__') return '未分配工种 - 领取次数'
  const w = workTypes.value.find((x) => x.id === wt)
  return w ? `${w.name} - 领取次数` : '领取次数'
})

const barOption = computed(() => {
  const list = filteredRows.value
  // 全部 → top 10；指定工种 → 全量。
  const show = workTypeFilter.value === '__all__'
    ? list.slice().sort((a, b) => b.pickup_count - a.pickup_count).slice(0, 10)
    : list.slice().sort((a, b) => b.pickup_count - a.pickup_count)
  // 倒序让 bar 从上到下递增（yAxis 默认下小上大）
  const sorted = show.slice().reverse()
  const names = sorted.map((r) => r.worker_name)
  const counts = sorted.map((r) => r.pickup_count)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: names, axisLabel: { fontSize: 12 } },
    series: [
      {
        type: 'bar',
        data: counts,
        itemStyle: { color: '#409eff' },
        label: { show: true, position: 'right', formatter: '{c}' },
      },
    ],
  }
})
</script>

<style lang="scss" scoped>
.worker-stats-tab {
  padding: 16px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-card :deep(.el-card__body) {
  padding-bottom: 0;
}

.chart-title {
  font-weight: 600;
  font-size: 14px;
}
</style>