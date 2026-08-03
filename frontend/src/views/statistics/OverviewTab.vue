<!--
  OverviewTab.vue

  Tab 1 - 车间生产总览（/statistics 默认 tab）。

  展示：
    - KPI 卡片（3 行）：基础统计 + 交付表现 + 返修
    - 折线图：daily_created vs daily_completed
    - 饼图：delivery_performance（on_time / orange / red）
    - 饼图：status_distribution（当前各状态零件数）

  日期范围变化时自动 reload；初次 mount 也拉一次。
-->

<template>
  <div v-loading="loading" class="overview-tab" element-loading-text="加载中">
    <template v-if="data">
      <!-- KPI 卡片 -->
      <el-row :gutter="12" class="kpi-row">
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="期内新建数" :value="data.created_count">
              <template #suffix>件</template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="期内完成数" :value="data.completed_count">
              <template #suffix>件</template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="期末在制数" :value="data.in_process_count">
              <template #suffix>件</template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic
              title="期内总产值"
              :value="valueYuan"
              :precision="2"
              prefix="¥"
            />
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="kpi-row">
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="期内交付数" :value="data.delivered_count">
              <template #suffix>件</template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="橙档晚交数" :value="data.late_orange_count">
              <template #suffix>
                <el-tag type="warning" size="small" effect="plain">橙档</el-tag>
              </template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="红档晚交数" :value="data.late_red_count">
              <template #suffix>
                <el-tag type="danger" size="small" effect="plain">红档</el-tag>
              </template>
            </el-statistic>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <el-card shadow="never" class="kpi-card">
            <el-statistic title="超期未交付数" :value="data.overdue_undelivered_count">
              <template #title>
                <span>超期未交付数</span>
                <el-tooltip
                  effect="dark"
                  content="当前快照，不受时间筛选影响"
                  placement="top"
                >
                  <el-icon style="margin-left: 4px" :size="12"><Warning /></el-icon>
                </el-tooltip>
              </template>
              <template #suffix>
                <el-tag type="danger" size="small" effect="plain">超期</el-tag>
              </template>
            </el-statistic>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="kpi-row">
        <el-col :xs="24" :sm="24" :md="8">
          <el-card shadow="never" class="kpi-card">
            <el-statistic
              title="期内返修工单数"
              :value="data.repair_part_count"
              :value-style="{ color: '#e6a23c' }"
            >
              <template #suffix>件</template>
            </el-statistic>
          </el-card>
        </el-col>
      </el-row>

      <!-- 图表 -->
      <el-row :gutter="12" class="chart-row">
        <el-col :xs="24" :md="16">
          <el-card shadow="never">
            <template #header>
              <span class="chart-title">每日新建 vs 每日完成</span>
            </template>
            <EChart :option="dailyOption" height="320px" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-card shadow="never">
            <template #header>
              <span class="chart-title">期内交付表现</span>
            </template>
            <EChart :option="deliveryOption" height="320px" />
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="chart-row">
        <el-col :span="24">
          <el-card shadow="never">
            <template #header>
              <span class="chart-title">当前零件状态分布</span>
            </template>
            <EChart :option="statusOption" height="320px" />
          </el-card>
        </el-col>
      </el-row>
    </template>

    <el-empty v-else-if="!loading" description="暂无数据" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning } from '@element-plus/icons-vue'
import EChart from '@/components/EChart.vue'
import { fetchOverview } from '@/api/statistics'
import type { OverviewOut } from '@/types/statistics'
import type { OrderStatus } from '@/types/parts'
import { STATUS_LABEL, STATUS_TAG_TYPE } from '@/constants/partStatus'

interface Props {
  dateFrom: string
  dateTo: string
}
const props = defineProps<Props>()

const loading = ref(false)
const data = ref<OverviewOut | null>(null)

async function reload(): Promise<void> {
  if (!props.dateFrom || !props.dateTo) return
  loading.value = true
  try {
    data.value = await fetchOverview({
      date_from: props.dateFrom,
      date_to: props.dateTo,
    })
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载概览失败')
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch(() => [props.dateFrom, props.dateTo], reload)

// 期内总产值：后端 delivered_value 是 string（Decimal 序列化为字符串保精度），
// el-statistic 接受 number；这里转 number。
const valueYuan = computed<number>(() => {
  if (!data.value) return 0
  const v = Number(data.value.delivered_value)
  return Number.isFinite(v) ? v : 0
})

// ============== ECharts options ==============

const dailyOption = computed(() => {
  const created = data.value?.daily_created ?? []
  const completed = data.value?.daily_completed ?? []
  const dates = created.map((d) => d.date)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['每日新建', '每日完成'], top: 0 },
    grid: { left: 40, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '每日新建',
        type: 'line',
        smooth: true,
        data: created.map((d) => d.count),
        itemStyle: { color: '#409eff' },
        areaStyle: { opacity: 0.15 },
      },
      {
        name: '每日完成',
        type: 'line',
        smooth: true,
        data: completed.map((d) => d.count),
        itemStyle: { color: '#67c23a' },
        areaStyle: { opacity: 0.15 },
      },
    ],
  }
})

const deliveryOption = computed(() => {
  const perf = data.value?.delivery_performance ?? { on_time: 0, orange: 0, red: 0 }
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 件 ({d}%)' },
    legend: { bottom: 0 },
    series: [
      {
        name: '交付表现',
        type: 'pie',
        radius: ['45%', '70%'],
        avoidLabelOverlap: false,
        label: { show: true, formatter: '{b}\n{d}%' },
        data: [
          { value: perf.on_time, name: '准时', itemStyle: { color: '#67c23a' } },
          { value: perf.orange, name: '橙档', itemStyle: { color: '#e6a23c' } },
          { value: perf.red, name: '红档', itemStyle: { color: '#f56c6c' } },
        ],
      },
    ],
  }
})

const statusOption = computed(() => {
  const items = data.value?.status_distribution ?? []
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 件 ({d}%)' },
    legend: { type: 'scroll', orient: 'vertical', left: 0, top: 'middle' },
    series: [
      {
        name: '状态分布',
        type: 'pie',
        radius: ['30%', '70%'],
        center: ['65%', '50%'],
        avoidLabelOverlap: true,
        label: { show: true, formatter: '{b}\n{d}%' },
        data: items.map((s) => ({
          value: s.count,
          name: STATUS_LABEL[s.status_value as OrderStatus] ?? s.status_value,
          itemStyle: tagColor(s.status_value),
        })),
      },
    ],
  }
})

// el-tag type → 简化的 hex，用于饼图。
function tagColor(statusValue: string): string {
  const t = STATUS_TAG_TYPE[statusValue as OrderStatus]
  switch (t) {
    case 'success': return '#67c23a'
    case 'warning': return '#e6a23c'
    case 'danger':  return '#f56c6c'
    case 'primary': return '#409eff'
    case 'info':
    default:        return '#909399'
  }
}
</script>

<style lang="scss" scoped>
.overview-tab {
  padding: 16px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.kpi-row { width: 100%; }

.kpi-card :deep(.el-card__body) {
  padding: 16px;
}

.chart-title {
  font-weight: 600;
  font-size: 14px;
}
</style>