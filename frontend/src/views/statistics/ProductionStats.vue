<!--
  ProductionStats.vue

  生产统计总入口（/statistics），挂在 MainLayout 下。

  四个 tab：
    - overview      车间生产总览（KPI 卡 + 折线 + 饼图）
    - workers       工人报工总览（工种筛选 + 水平条形 + 工人表）
    - worker-detail 单工人详情（折线 + 参与工单表）
    - pickup-skips  跳序取件统计（汇总表 + drawer 明细分页；2026-08-05）

  顶部筛选区为时间范围（快捷按钮 / 自定义日期对），传 dateFrom / dateTo
  两个 ISO 'YYYY-MM-DD' 给子组件，子组件各自 watch 拉数。

  activeTab 切换**不**重发请求——只有 dateFrom / dateTo 变化才推送给子组件。
  选中工人后切到 worker-detail tab，并通过 ref 传 workerId。
  pickup-skips tab 不消费 dateFrom / dateTo（append-only 历史流）。
-->

<template>
  <div class="production-stats">
    <el-page-header class="page-header" :icon="DataAnalysis">
      <template #content>
        <div class="header-row">
          <span class="header-title">生产统计</span>
          <span class="header-sub">车间生产与工人报工总览</span>
        </div>
      </template>
    </el-page-header>

    <el-card shadow="never" class="filter-card">
      <el-form inline @submit.prevent>
        <el-form-item label="时间范围">
          <el-radio-group v-model="rangePreset" @change="onPresetChange">
            <el-radio-button label="this-month">本月</el-radio-button>
            <el-radio-button label="last-month">上月</el-radio-button>
            <el-radio-button label="this-year">本年</el-radio-button>
            <el-radio-button label="custom">自定义</el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-if="rangePreset === 'custom'"
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="margin-left: 12px; width: 260px"
            @change="onCustomRangeChange"
          />
          <span v-else class="range-text">
            {{ dateRange[0] }} ~ {{ dateRange[1] }}
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <el-tabs v-model="activeTab" type="border-card" class="stats-tabs">
      <el-tab-pane label="车间生产总览" name="overview">
        <OverviewTab
          v-if="dateFrom"
          :date-from="dateFrom"
          :date-to="dateTo"
        />
      </el-tab-pane>
      <el-tab-pane label="工人报工总览" name="workers">
        <WorkerStatsTab
          v-if="dateFrom"
          :date-from="dateFrom"
          :date-to="dateTo"
          @select-worker="onSelectWorker"
        />
      </el-tab-pane>
      <el-tab-pane label="单工人详情" name="worker-detail">
        <WorkerDetailTab
          v-if="dateFrom"
          :date-from="dateFrom"
          :date-to="dateTo"
          :worker-id="selectedWorkerId"
        />
      </el-tab-pane>
      <el-tab-pane label="跳序取件" name="pickup-skips">
        <PickupSkipTab />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { DataAnalysis } from '@element-plus/icons-vue'
import OverviewTab from './OverviewTab.vue'
import WorkerStatsTab from './WorkerStatsTab.vue'
import WorkerDetailTab from './WorkerDetailTab.vue'
import PickupSkipTab from './PickupSkipTab.vue'

type RangePreset = 'this-month' | 'last-month' | 'this-year' | 'custom'

function toIsoDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}
function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}
function startOfYear(d: Date): Date {
  return new Date(d.getFullYear(), 0, 1)
}
function endOfYear(d: Date): Date {
  return new Date(d.getFullYear(), 11, 31)
}

function presetRange(p: RangePreset): [string, string] {
  const now = new Date()
  if (p === 'this-month') {
    return [toIsoDate(startOfMonth(now)), toIsoDate(now)]
  }
  if (p === 'last-month') {
    const lastMonthRef = new Date(now.getFullYear(), now.getMonth() - 1, 1)
    return [toIsoDate(startOfMonth(lastMonthRef)), toIsoDate(endOfMonth(lastMonthRef))]
  }
  if (p === 'this-year') {
    return [toIsoDate(startOfYear(now)), toIsoDate(endOfYear(now))]
  }
  // 自定义：默认本月
  return [toIsoDate(startOfMonth(now)), toIsoDate(now)]
}

const rangePreset = ref<RangePreset>('this-month')
const dateRange = ref<[string, string]>(presetRange('this-month'))

const dateFrom = computed<string | null>(() => {
  const r = dateRange.value
  return r && r[0] ? r[0] : null
})
const dateTo = computed<string>(() => {
  const r = dateRange.value
  return r && r[1] ? r[1] : (r && r[0] ? r[0] : '')
})

function onPresetChange(p: string | number | boolean | undefined): void {
  if (!p) return
  if (p === 'custom') {
    // 用户改完日期后由 onCustomRangeChange 兜底；如果直接点自定义，先填本月范围占位。
    if (!dateRange.value || !dateRange.value[0]) {
      dateRange.value = presetRange('this-month')
    }
    return
  }
  dateRange.value = presetRange(p as RangePreset)
}

function onCustomRangeChange(v: [string, string] | null): void {
  if (!v || !v[0] || !v[1]) {
    ElMessage.warning('请选择开始和结束日期')
    return
  }
  dateRange.value = [v[0], v[1]]
}

// 当 dateRange 直接被改（preset 切换 / 初始值），推送给子组件由 watch 触发。
// 这里不额外写 watch，因为 dateFrom/dateTo 是 computed，子组件 watch 已能响应。

const activeTab = ref<'overview' | 'workers' | 'worker-detail' | 'pickup-skips'>('overview')
const selectedWorkerId = ref<string | null>(null)

function onSelectWorker(workerId: string): void {
  selectedWorkerId.value = workerId
  activeTab.value = 'worker-detail'
}

// 切到 worker-detail 后如果 workerId 是 null（例如用户没选过工人），自动切回 overview。
watch(activeTab, (t) => {
  if (t === 'worker-detail' && !selectedWorkerId.value) {
    activeTab.value = 'overview'
  }
})
</script>

<style lang="scss" scoped>
.production-stats {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.page-header {
  background: #fff;
  padding: 12px 16px;
  border-radius: 4px;
}

.header-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.header-title {
  font-size: 20px;
  font-weight: 600;
}

.header-sub {
  font-size: 13px;
  color: var(--text-secondary);
}

.filter-card :deep(.el-card__body) {
  padding-bottom: 0;
}

.range-text {
  margin-left: 12px;
  color: var(--text-secondary);
  font-size: 13px;
}

.stats-tabs {
  background: #fff;
  border-radius: 4px;
}
</style>