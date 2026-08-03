<!--
  WorkerDetailTab.vue

  Tab 3 - 单工人详情。

  顶部：工人选择器（filterable），优先从 tab2 缓存挑；未加载过 tab2 时提示用户
  先到「工人报工总览」加载一次（不发新端点，与任务约束一致）。

  卡片行：领取次数 / 领取件数 / 参与工单数 / 归还次数（el-statistic）。

  图：每日领取趋势折线图。

  表：参与工单一览（serial_no / 名称 / 图号 / 当前状态 / 我的领取次数 / 最近领取时间
     / 操作 → 跳零件详情）。

  workerId 为 null → el-empty「请选择工人」。
-->

<template>
  <div class="worker-detail-tab">
    <!-- 工人选择器 -->
    <el-card shadow="never" class="picker-card">
      <el-form inline @submit.prevent>
        <el-form-item label="选择工人">
          <el-select
            v-model="selectedWorkerId"
            placeholder="请先在「工人报工总览」加载一次后，再在此选择工人"
            filterable
            clearable
            style="width: 320px"
            @change="onPickerChange"
          >
            <el-option
              v-for="r in workerOptions"
              :key="r.worker_id"
              :label="`${r.worker_name}（${r.badge_code}）${r.is_active ? '' : ' · 已停用'}`"
              :value="r.worker_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!hasCache">
          <el-button type="primary" :loading="warmingCache" @click="warmCache">
            加载工人列表
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <template v-if="!workerId">
      <el-empty description="请选择工人" />
    </template>

    <template v-else>
      <div v-loading="loading" element-loading-text="加载中">
        <template v-if="data">
          <!-- 顶部 worker 名 -->
          <el-card shadow="never" class="worker-card">
            <div class="worker-head">
              <span class="worker-name">{{ data.worker.name }}</span>
              <span class="worker-badge">工牌：{{ data.worker.badge_code }}</span>
              <el-tag v-if="data.worker.work_type_name" type="primary" size="small" effect="plain">
                {{ data.worker.work_type_name }}
              </el-tag>
              <el-tag v-if="!data.worker.is_active" type="info" size="small" effect="plain">
                已停用
              </el-tag>
            </div>
          </el-card>

          <!-- KPI 卡片 -->
          <el-row :gutter="12" class="kpi-row">
            <el-col :xs="12" :sm="12" :md="6">
              <el-card shadow="never" class="kpi-card">
                <el-statistic title="领取次数" :value="data.pickup_count">
                  <template #suffix>次</template>
                </el-statistic>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="12" :md="6">
              <el-card shadow="never" class="kpi-card">
                <el-statistic title="领取件数" :value="data.pickup_quantity">
                  <template #suffix>件</template>
                </el-statistic>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="12" :md="6">
              <el-card shadow="never" class="kpi-card">
                <el-statistic
                  title="参与工单数"
                  :value="data.participated_part_count"
                >
                  <template #suffix>件</template>
                </el-statistic>
              </el-card>
            </el-col>
            <el-col :xs="12" :sm="12" :md="6">
              <el-card shadow="never" class="kpi-card">
                <el-statistic title="归还次数" :value="data.return_count">
                  <template #suffix>次</template>
                </el-statistic>
              </el-card>
            </el-col>
          </el-row>

          <!-- 趋势图 -->
          <el-card shadow="never">
            <template #header>
              <span class="chart-title">每日领取趋势</span>
            </template>
            <EChart :option="pickupTrendOption" height="280px" />
          </el-card>

          <!-- 参与工单表 -->
          <el-card shadow="never">
            <template #header>
              <span class="chart-title">参与工单一览（共 {{ data.parts.length }} 件）</span>
            </template>
            <el-table
              :data="data.parts"
              row-key="part_id"
              stripe
              border
              size="default"
              empty-text="该工人期内未参与工单"
            >
              <el-table-column label="流水号" min-width="160" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.serial_no" type="primary" size="small" effect="plain">
                    {{ row.serial_no }}
                  </el-tag>
                  <span v-else style="color: #c0c4cc">—</span>
                </template>
              </el-table-column>
              <el-table-column label="名称" min-width="200" align="center" show-overflow-tooltip>
                <template #default="{ row }">
                  <router-link :to="`/parts/${row.part_id}`" class="name-link">
                    {{ row.name }}
                  </router-link>
                </template>
              </el-table-column>
              <el-table-column prop="drawing_no" label="图号" min-width="140" align="center" />
              <el-table-column label="当前状态" min-width="100" align="center">
                <template #default="{ row }">
                  <el-tag
                    :type="STATUS_TAG_TYPE[row.status as OrderStatus] ?? 'info'"
                    size="small"
                    effect="plain"
                  >
                    {{ STATUS_LABEL[row.status as OrderStatus] ?? row.status }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="pickup_count" label="我的领取次数" min-width="110" align="center" />
              <el-table-column prop="last_pickup_at" label="最近领取时间" min-width="170" align="center" />
              <el-table-column label="操作" min-width="100" fixed="right" align="center">
                <template #default="{ row }">
                  <el-button
                    link
                    type="primary"
                    size="small"
                    @click="goPartDetail(row.part_id)"
                  >详情</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import EChart from '@/components/EChart.vue'
import { fetchWorkerDetail, fetchWorkerStats } from '@/api/statistics'
import type { WorkerDetailOut, WorkerStatsItem } from '@/types/statistics'
import type { OrderStatus } from '@/types/parts'
import { STATUS_LABEL, STATUS_TAG_TYPE } from '@/constants/partStatus'

interface Props {
  dateFrom: string
  dateTo: string
  workerId: string | null
}
const props = defineProps<Props>()

const router = useRouter()

const selectedWorkerId = ref<string | null>(props.workerId)
const workerOptions = ref<WorkerStatsItem[]>([])
const hasCache = computed(() => workerOptions.value.length > 0)
const warmingCache = ref(false)
const loading = ref(false)
const data = ref<WorkerDetailOut | null>(null)

function onPickerChange(v: string | null): void {
  selectedWorkerId.value = v
}

async function warmCache(): Promise<void> {
  warmingCache.value = true
  try {
    const res = await fetchWorkerStats({
      date_from: props.dateFrom,
      date_to: props.dateTo,
    })
    workerOptions.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工人列表失败')
  } finally {
    warmingCache.value = false
  }
}

async function reload(): Promise<void> {
  if (!selectedWorkerId.value) {
    data.value = null
    return
  }
  if (!props.dateFrom || !props.dateTo) return
  loading.value = true
  try {
    data.value = await fetchWorkerDetail(selectedWorkerId.value, {
      date_from: props.dateFrom,
      date_to: props.dateTo,
    })
    // 顺便把 worker 信息补到 options（即使 tab2 没加载过）
    if (!workerOptions.value.some((w) => w.worker_id === data.value!.worker.id)) {
      // 不强求；picker 只在已有 options 时才能选。
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工人详情失败')
  } finally {
    loading.value = false
  }
}

// watch external workerId（来自父组件 emit），并 reload
watch(
  () => props.workerId,
  (v) => {
    if (v) {
      selectedWorkerId.value = v
      void reload()
    } else {
      selectedWorkerId.value = null
      data.value = null
    }
  },
)
watch(() => [selectedWorkerId.value, props.dateFrom, props.dateTo], reload)

onMounted(async () => {
  // 如果 props.workerId 有（用户从 tab2 点过来），立即拉数；options 暂为空。
  if (props.workerId) {
    selectedWorkerId.value = props.workerId
    await reload()
  }
})

function goPartDetail(partId: string): void {
  router.push(`/parts/${partId}`)
}

// ============== ECharts options ==============

const pickupTrendOption = computed(() => {
  const days = data.value?.daily_pickups ?? []
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: days.map((d) => d.date), boundaryGap: false },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '每日领取次数',
        type: 'line',
        smooth: true,
        data: days.map((d) => d.count),
        itemStyle: { color: '#409eff' },
        areaStyle: { opacity: 0.2 },
      },
    ],
  }
})
</script>

<style lang="scss" scoped>
.worker-detail-tab {
  padding: 16px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.picker-card :deep(.el-card__body) {
  padding-bottom: 0;
}

.worker-card :deep(.el-card__body) {
  padding: 12px 16px;
}

.worker-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.worker-name {
  font-size: 18px;
  font-weight: 600;
}

.worker-badge {
  color: var(--text-secondary);
  font-size: 13px;
}

.kpi-row { width: 100%; }

.kpi-card :deep(.el-card__body) {
  padding: 16px;
}

.chart-title {
  font-weight: 600;
  font-size: 14px;
}

.name-link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.name-link:hover {
  text-decoration: underline;
}
</style>