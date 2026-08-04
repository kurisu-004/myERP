<!--
  PickupSkipTab.vue

  Tab 4 - 跳序取件统计。

  顶部：汇总 el-table（按工人聚合 skip_count desc / last_skip_at desc）。
        行点击 → el-drawer（标题「工人跳序明细」），内嵌分页 el-pagination +
        明细 el-table（时间 / 序列号 / 零件名 / 批次号 / 数量 / 所取交期 / 被跳过交期）。

  无日期范围：append-only 历史流。

  Element Plus 合规（CLAUDE.md §Element Plus）：
  - el-table / el-pagination / el-drawer / el-tag / el-empty / v-loading / ElMessage
  - 参见 references/table.md / feedback.md。
-->

<template>
  <div v-loading="summaryLoading" class="pickup-skip-tab" element-loading-text="加载中">
    <!-- 汇总表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="card-title">跳序取件汇总（按工人）</span>
          <el-button size="small" :loading="summaryLoading" @click="reloadSummary">
            刷新
          </el-button>
        </div>
      </template>

      <el-table
        :data="summaryRows"
        row-key="worker_id"
        stripe
        border
        size="default"
        empty-text="暂无跳序记录"
        @row-click="onRowClick"
      >
        <el-table-column
          prop="worker_name"
          label="工人"
          min-width="120"
          align="center"
        >
          <template #default="{ row }">
            <span>{{ row.worker_name }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="badge_code"
          label="工牌"
          min-width="140"
          align="center"
        />
        <el-table-column
          prop="work_type_name"
          label="工种"
          min-width="140"
          align="center"
        >
          <template #default="{ row }">
            <el-tag
              v-if="row.work_type_name"
              type="primary"
              size="small"
              effect="plain"
            >
              {{ row.work_type_name }}
            </el-tag>
            <span v-else style="color: #c0c4cc">—</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="skip_count"
          label="跳序次数"
          min-width="100"
          align="center"
          sortable
        >
          <template #default="{ row }">
            <el-tag
              :type="row.skip_count >= 5 ? 'danger' : row.skip_count >= 2 ? 'warning' : 'info'"
              size="small"
              effect="plain"
            >
              {{ row.skip_count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="last_skip_at"
          label="最近跳序时间"
          min-width="180"
          align="center"
        >
          <template #default="{ row }">
            <span v-if="row.last_skip_at">{{ formatDateTime(row.last_skip_at) }}</span>
            <span v-else style="color: #c0c4cc">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 明细抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      direction="rtl"
      size="60%"
      :with-header="true"
      :destroy-on-close="false"
      :append-to-body="true"
    >
      <template #header>
        <div class="drawer-header">
          <span class="drawer-title">工人跳序明细</span>
          <span v-if="currentWorker" class="drawer-sub">
            {{ currentWorker.worker_name }}（{{ currentWorker.badge_code }}）·
            共 {{ detailData?.total ?? 0 }} 条
          </span>
        </div>
      </template>

      <div v-loading="detailLoading" element-loading-text="加载中">
        <el-table
          :data="detailData?.items ?? []"
          row-key="id"
          stripe
          border
          size="default"
          empty-text="该工人暂无跳序记录"
        >
          <el-table-column label="时间" min-width="170" align="center">
            <template #default="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column label="流水号" min-width="160" align="center">
            <template #default="{ row }">
              <el-tag
                v-if="row.serial_no"
                type="primary"
                size="small"
                effect="plain"
              >
                {{ row.serial_no }}
              </el-tag>
              <span v-else style="color: #c0c4cc">—</span>
            </template>
          </el-table-column>
          <el-table-column
            prop="part_name"
            label="零件名"
            min-width="200"
            align="center"
            show-overflow-tooltip
          />
          <el-table-column
            prop="batch_no"
            label="批次号"
            min-width="80"
            align="center"
          />
          <el-table-column
            prop="quantity"
            label="数量"
            min-width="80"
            align="center"
          />
          <el-table-column
            prop="part_planned_delivery_date"
            label="所取交期"
            min-width="120"
            align="center"
          >
            <template #default="{ row }">
              <span v-if="row.part_planned_delivery_date">
                {{ row.part_planned_delivery_date }}
              </span>
              <span v-else style="color: #c0c4cc">—</span>
            </template>
          </el-table-column>
          <el-table-column
            prop="skipped_earliest_date"
            label="被跳过交期"
            min-width="120"
            align="center"
          >
            <template #default="{ row }">
              <span v-if="row.skipped_earliest_date">
                <el-tag type="danger" size="small" effect="plain">
                  {{ row.skipped_earliest_date }}
                </el-tag>
              </span>
              <span v-else style="color: #c0c4cc">—</span>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="detailData && detailData.total > 0"
          v-model:current-page="detailPage"
          v-model:page-size="detailPageSize"
          :total="detailData.total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          class="detail-pagination"
          @current-change="reloadDetail"
          @size-change="onPageSizeChange"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  fetchPickupSkipDetail,
  fetchPickupSkipSummary,
} from '@/api/statistics'
import type {
  PickupSkipDetailOut,
  PickupSkipSummaryItem,
} from '@/types/statistics'

// ========== 汇总 ==========
const summaryLoading = ref(false)
const summaryRows = ref<PickupSkipSummaryItem[]>([])

async function reloadSummary(): Promise<void> {
  summaryLoading.value = true
  try {
    const res = await fetchPickupSkipSummary()
    summaryRows.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载汇总失败')
  } finally {
    summaryLoading.value = false
  }
}

// ========== 抽屉 + 明细 ==========
const drawerVisible = ref(false)
const detailLoading = ref(false)
const currentWorker = ref<PickupSkipSummaryItem | null>(null)
const detailData = ref<PickupSkipDetailOut | null>(null)
const detailPage = ref(1)
const detailPageSize = ref(20)

const detailOffset = computed<number>(() => (detailPage.value - 1) * detailPageSize.value)

async function reloadDetail(): Promise<void> {
  if (!currentWorker.value) return
  detailLoading.value = true
  try {
    detailData.value = await fetchPickupSkipDetail(currentWorker.value.worker_id, {
      limit: detailPageSize.value,
      offset: detailOffset.value,
    })
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载明细失败')
  } finally {
    detailLoading.value = false
  }
}

function onRowClick(row: PickupSkipSummaryItem): void {
  currentWorker.value = row
  detailPage.value = 1
  detailPageSize.value = 20
  drawerVisible.value = true
  void reloadDetail()
}

function onPageSizeChange(size: number): void {
  detailPageSize.value = size
  detailPage.value = 1
  void reloadDetail()
}

function formatDateTime(iso: string): string {
  // 后端 ISO → 'YYYY-MM-DD HH:mm'，去掉秒数与时区，节省列宽
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/)
  if (m) return `${m[1]} ${m[2]}`
  return iso
}

onMounted(reloadSummary)
</script>

<style lang="scss" scoped>
.pickup-skip-tab {
  padding: 16px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  font-weight: 600;
  font-size: 14px;
}

.drawer-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
}

.drawer-sub {
  color: var(--text-secondary);
  font-size: 13px;
}

.detail-pagination {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>