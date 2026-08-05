<template>
  <div class="dashboard">
    <!-- 顶部 2/3：货架轮播（每页 2 个货架卡片并排） -->
    <section class="shelves-area">
      <div v-if="shelfGroups.length === 0" class="shelves-empty">暂无货架上的零件</div>
      <el-carousel
        v-else
        class="shelves-carousel"
        height="100%"
        :interval="8000"
        arrow="always"
        :pause-on-hover="true"
      >
        <el-carousel-item v-for="(page, pageIdx) in shelfPages" :key="pageIdx">
          <div class="shelf-page">
            <div
              v-for="g in page"
              :key="g.shelf_id"
              class="shelf-card"
            >
              <div class="shelf-card-head">
                <span class="shelf-code">{{ g.shelf_code }}</span>
                <span class="shelf-name">{{ g.shelf_name }}</span>
                <span class="shelf-count">{{ g.total_count ?? g.items.length }} 件</span>
              </div>
              <div class="shelf-card-body">
                <template v-if="g.items.length > 0">
                  <div
                    v-for="item in g.items.slice(0, 10)"
                    :key="item.batch_id || item.id"
                    :class="['shelf-item', { urgent: item.is_urgent }]"
                  >
                    <span
                      :class="['item-serial', { 'is-clickable': canOpenPartDetail }]"
                      :title="canOpenPartDetail ? '查看详情' : ''"
                      @click="canOpenPartDetail && goPartDetail(item.id)"
                    >{{ item.serial_no || '—' }}</span>
                    <span class="item-name" :title="item.name">{{ item.name }}</span>
                    <span class="item-process" :title="item.next_process_name || ''">
                      {{ item.next_process_name || '—' }}
                    </span>
                    <span class="item-due">{{ formatDashboardDeliveryDate(item.planned_delivery_date) }}</span>
                  </div>
                </template>
                <div v-else class="shelf-empty">空</div>
              </div>
            </div>
          </div>
        </el-carousel-item>
      </el-carousel>
    </section>

    <!-- 底部 1/3：正在加工（按工人分组，只保留姓名+流水号 chips） -->
    <section class="inprocess-area">
      <div class="inprocess-card">
        <div class="inprocess-head">
          <span class="inprocess-title"><el-icon class="title-icon"><Tools /></el-icon>正在加工</span>
          <span class="inprocess-count">{{ workerParts.length }} 件</span>
        </div>
        <div class="inprocess-items">
          <template v-if="workerGroups.length > 0">
            <div
              v-for="group in workerGroups"
              :key="group.key"
              class="worker-group"
            >
              <div class="worker-name">{{ group.worker_name || '未记录' }}</div>
              <div class="worker-chips">
                <span
                  v-for="item in group.items"
                  :key="item.batch_id || item.id"
                  :class="['worker-chip', { urgent: item.is_urgent, 'is-clickable': canOpenPartDetail }]"
                  :title="canOpenPartDetail ? '查看详情' : ''"
                  @click="canOpenPartDetail && goPartDetail(item.id)"
                >
                  {{ item.serial_no || '—' }}
                </span>
              </div>
            </div>
          </template>
          <div v-else class="inprocess-empty">暂无正在加工的零件</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Tools } from '@element-plus/icons-vue'
import { onDashboardSnapshot } from '@/api/dashboard'
import { usePermissions } from '@/composables/usePermissions'
import type {
  DashboardPartItem,
  DashboardShelfGroup,
  DashboardSnapshot,
} from '@/types/dashboard'
import { formatDashboardDeliveryDate } from '@/utils/deliveryDate'

const router = useRouter()
const { isManager, isClerk, isInspector, isCncProgrammer } = usePermissions()

// 工控机账号（纯 SHELF_ACCOUNT）禁跳详情；与后端 GET /parts/{id} 读权限对齐
const canOpenPartDetail = computed(
  () => isManager.value || isClerk.value || isInspector.value || isCncProgrammer.value,
)

function goPartDetail(id: string): void {
  router.push(`/parts/${id}`)
}

const shelfGroups = ref<DashboardShelfGroup[]>([])
const workerParts = ref<DashboardSnapshot['data']['in_process']>([])

let offSnap: (() => void) | null = null

function applySnapshot(snap: DashboardSnapshot): void {
  shelfGroups.value = snap.data.on_production_shelves
  workerParts.value = snap.data.in_process
}

// ============ 货架轮播分页 ============
const shelfPages = computed(() => {
  const groups = shelfGroups.value
  const pages: DashboardShelfGroup[][] = []
  for (let i = 0; i < groups.length; i += 2) {
    pages.push(groups.slice(i, i + 2))
  }
  return pages
})

// ============ 工人分组 ============
interface WorkerGroup {
  key: string
  worker_name: string | null
  items: DashboardPartItem[]
}

const workerGroups = computed(() => {
  const map = new Map<string, WorkerGroup>()
  for (const p of workerParts.value) {
    const key = String(p.current_holder_id ?? p.worker_name ?? 'unknown')
    const existing = map.get(key)
    if (existing) {
      existing.items.push(p)
    } else {
      map.set(key, { key, worker_name: p.worker_name ?? null, items: [p] })
    }
  }
  return Array.from(map.values())
})

onMounted(() => {
  offSnap = onDashboardSnapshot(applySnapshot)
})

onBeforeUnmount(() => {
  offSnap?.(); offSnap = null
})
</script>

<style lang="scss" scoped>
.dashboard {
  height: calc(100vh - 60px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 12px;
  box-sizing: border-box;
  gap: 12px;
}

// ============ 顶部 2/3：货架轮播 ============
.shelves-area {
  flex: 2;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.shelves-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  background: #fff;
  border-radius: 6px;
  font-size: 14px;
}
.shelves-carousel {
  flex: 1;
  min-height: 0;
}
.shelf-page {
  display: flex;
  gap: 12px;
  height: 100%;
  padding: 0 4px;
}
.shelf-page .shelf-card {
  flex: 1;
  min-width: 0;
}
.shelf-card {
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  min-height: 0;
}
.shelf-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #eee;
  background: #fafbfc;
  flex-shrink: 0;
}
.shelf-code {
  font-weight: 600;
  font-size: 14px;
  color: var(--primary-color);
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.shelf-name { font-size: 13px; color: var(--text-secondary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shelf-count {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--el-color-primary-light-9);
  color: var(--primary-color);
  padding: 1px 8px;
  border-radius: 10px;
}
.shelf-card-body {
  flex: 1;
  overflow: hidden;
  padding: 4px 0;
  min-height: 0;
}
.shelf-item {
  display: grid;
  grid-template-columns: 96px 1.4fr 1fr 80px;   /* 序号 | 名称 | 下一工序 | 交期 */
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 13px;
  border-bottom: 1px dashed #f0f0f0;
  &.urgent { background: #fde2e2; }
  &:last-child { border-bottom: none; }
}
.item-serial {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-weight: 600;
  font-size: 12px;
  color: var(--text-primary);
}
.item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}
.item-process {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--primary-color);
  font-size: 13px;
}
.item-due {
  color: #888;
  text-align: right;
  font-size: 12px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.shelf-empty {
  padding: 24px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 12px;
}

// ============ 底部 1/3：正在加工（按工人分组） ============
.inprocess-area {
  flex: 1;
  min-height: 0;
  display: flex;
}
.inprocess-card {
  flex: 1;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.inprocess-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #eee;
  background: #fafbfc;
  flex-shrink: 0;
}
.inprocess-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 15px;
}
.inprocess-count {
  font-size: 12px;
  color: var(--primary-color);
  background: var(--el-color-primary-light-9);
  padding: 1px 8px;
  border-radius: 10px;
}
.inprocess-items {
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 8px;
  padding: 10px 12px;
  overflow: hidden;
  min-height: 0;
}
.worker-group {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.worker-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
}
.worker-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.worker-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  background: #fff;
  border-radius: 4px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  &.urgent { background: #fde2e2; color: #f56c6c; }
}
.inprocess-empty {
  width: 100%;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  padding: 20px 0;
}

// ============ 序列号可点态（仅非 SHELF_ACCOUNT 账号） ============
.item-serial.is-clickable,
.worker-chip.is-clickable {
  cursor: pointer;
}
.item-serial.is-clickable:hover,
.worker-chip.is-clickable:hover {
  text-decoration: underline;
}

// ============ 通用 ============

// ============================================================
// 车间大屏适配：1080p / 4K
// 视距 5-8m，ppi ≈ 40。×2 起点保证「抬头就能看清」最小字号 26px。
// ============================================================
@media (min-width: 1600px) {
  .inprocess-title                     { font-size: 32px; }
  .shelf-code                          { font-size: 28px; }
  .shelf-name                          { font-size: 26px; }
  .shelf-count, .inprocess-count       { font-size: 22px; padding: 4px 14px; }
  .shelf-item                          {
    font-size: 26px;
    padding: 14px 20px;
    gap: 12px;
    grid-template-columns: 140px 1.4fr 1fr 100px;
  }
  .item-serial                         { font-size: 28px; }
  .item-process                        { font-size: 26px; }
  .item-due                            { font-size: 24px; }
  .shelf-empty, .shelves-empty         { font-size: 26px; }
  .worker-group                        {
    padding: 10px 20px;
    gap: 12px;
    border-radius: 10px;
  }
  .worker-name                         { font-size: 24px; }
  .worker-chip                         {
    font-size: 22px;
    padding: 4px 12px;
    border-radius: 6px;
  }
  .inprocess-empty                     { font-size: 24px; padding: 32px 0; }
}

@media (min-width: 2400px) {
  .inprocess-title                     { font-size: 40px; }
  .shelf-code                          { font-size: 34px; }
  .shelf-name                          { font-size: 32px; }
  .shelf-count, .inprocess-count       { font-size: 28px; padding: 6px 18px; }
  .shelf-item                          {
    font-size: 32px;
    padding: 18px 28px;
    gap: 16px;
    grid-template-columns: 180px 1.4fr 1fr 120px;
  }
  .item-serial                         { font-size: 34px; }
  .item-process                        { font-size: 32px; }
  .item-due                            { font-size: 30px; }
  .shelf-empty, .shelves-empty         { font-size: 32px; }
  .worker-group                        {
    padding: 14px 28px;
    gap: 16px;
    border-radius: 12px;
  }
  .worker-name                         { font-size: 30px; }
  .worker-chip                         {
    font-size: 28px;
    padding: 6px 16px;
    border-radius: 8px;
  }
  .inprocess-empty                     { font-size: 30px; padding: 48px 0; }
}
</style>
