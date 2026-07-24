<template>
  <div class="dashboard">
    <!-- 顶部 2/3：货架网格（每个货架一个卡，auto-fit grid 横向并排） -->
    <section class="shelves-area">
      <div v-if="shelfGroups.length === 0" class="shelves-empty">暂无货架上的零件</div>
      <div v-else class="shelves-grid">
        <div
          v-for="g in shelfGroups"
          :key="g.shelf_id"
          class="shelf-card"
        >
          <div class="shelf-card-head">
            <span class="shelf-code">{{ g.shelf_code }}</span>
            <span class="shelf-name">{{ g.shelf_name }}</span>
            <span class="shelf-count">{{ g.items.length }} 件</span>
          </div>
          <div class="shelf-card-body">
            <template v-if="g.items.length > 0">
              <div
                v-for="item in g.items.slice(0, 10)"
                :key="item.id"
                :class="['shelf-item', { urgent: item.is_urgent }]"
              >
                <span class="item-serial">{{ item.serial_no || '—' }}</span>
                <span class="item-name" :title="item.name">{{ item.name }}</span>
                <span class="item-process" :title="item.next_process_name || ''">
                  {{ item.next_process_name || '—' }}
                </span>
                <span class="item-due">{{ formatShortDate(item.planned_delivery_date) }}</span>
              </div>
            </template>
            <div v-else class="shelf-empty">空</div>
          </div>
        </div>
      </div>
    </section>

    <!-- 底部 1/3：正在加工（flex-wrap pill 流式填充） -->
    <section class="inprocess-area">
      <div class="inprocess-card">
        <div class="inprocess-head">
          <span class="inprocess-title"><el-icon class="title-icon"><Tools /></el-icon>正在加工</span>
          <span class="inprocess-count">{{ workerParts.length }} 件</span>
        </div>
        <div class="inprocess-items">
          <div
            v-for="p in workerParts"
            :key="p.id"
            class="inprocess-pill"
          >
            <span class="pill-serial">{{ p.serial_no || '—' }}</span>
            <el-avatar :size="avatarSize" class="pill-avatar">
              <el-icon :size="avatarIconSize"><UserFilled /></el-icon>
            </el-avatar>
            <span class="pill-name">{{ p.worker_name || '未记录' }}</span>
            <span
              v-if="p.next_process_name"
              class="pill-process"
              :title="p.next_process_name"
            >→ {{ p.next_process_name }}</span>
          </div>
          <div v-if="workerParts.length === 0" class="inprocess-empty">暂无正在加工的零件</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Tools, UserFilled } from '@element-plus/icons-vue'
import { onDashboardSnapshot } from '@/api/dashboard'
import type {
  DashboardShelfGroup,
  DashboardSnapshot,
} from '@/types/dashboard'

const shelfGroups = ref<DashboardShelfGroup[]>([])
const workerParts = ref<DashboardSnapshot['data']['in_process']>([])

let offSnap: (() => void) | null = null

/** "YYYY-MM-DD..." -> "MM-DD"；空值原样返回。 */
function formatShortDate(s: string | null | undefined): string {
  if (!s) return '-'
  return s.length >= 10 ? s.slice(5) : s
}

function applySnapshot(snap: DashboardSnapshot): void {
  shelfGroups.value = snap.data.on_production_shelves
  workerParts.value = snap.data.in_process
}

// ============ 响应式字号/头像 ============
// 车间大屏 50"+：>=1600px 是 1080p 投影；>=2400px 是 4K。
// 这里把 avatar size 提到 script 而非 CSS，因为 el-avatar :size 是 prop（不是 CSS 字体）。
const winWidth = ref<number>(
  typeof window === 'undefined' ? 1280 : window.innerWidth
)
function syncWidth(): void {
  winWidth.value = window.innerWidth
}
const avatarSize = computed(() => {
  if (winWidth.value >= 2400) return 80
  if (winWidth.value >= 1600) return 64
  return 32
})
const avatarIconSize = computed(() => {
  if (winWidth.value >= 2400) return 40
  if (winWidth.value >= 1600) return 32
  return 18
})

onMounted(() => {
  offSnap = onDashboardSnapshot(applySnapshot)
  syncWidth()
  window.addEventListener('resize', syncWidth, { passive: true })
})

onBeforeUnmount(() => {
  offSnap?.(); offSnap = null
  window.removeEventListener('resize', syncWidth)
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

// ============ 顶部 2/3：货架网格 ============
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
.shelves-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  grid-auto-rows: minmax(0, 1fr);
  gap: 12px;
  min-height: 0;
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

// ============ 底部 1/3：正在加工 ============
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
.inprocess-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px 6px 6px;
  background: #f5f7fa;
  border-radius: 999px;
  font-size: 13px;
  height: 40px;
  box-sizing: border-box;
}
.pill-avatar {
  background: var(--el-color-primary-light-7);
  color: #fff;
  flex-shrink: 0;
}
.pill-serial {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-weight: 600;
  font-size: 12px;
  color: var(--text-primary);
  padding-left: 6px;
}
.pill-name {
  color: var(--text-primary);
  font-weight: 500;
}
.pill-process {
  color: var(--primary-color);
  font-size: 13px;
  font-weight: 500;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inprocess-empty {
  width: 100%;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  padding: 20px 0;
}

// ============ 通用 ============

// ============================================================
// 车间大屏适配：1080p / 4K
// 视距 5-8m，ppi ≈ 40。×2 起点保证「抬头就能看清」最小字号 26px；
// avatar/icon 是 Element Plus 的 prop（不是 CSS 字体），由 script 的 avatarSize / avatarIconSize 接管。
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
  .inprocess-pill                      {
    font-size: 24px;
    height: 72px;
    padding: 8px 20px 8px 8px;
    gap: 12px;
  }
  .pill-serial                         { font-size: 24px; padding-left: 10px; }
  .pill-name                           { font-size: 24px; }
  .pill-process                        { font-size: 22px; max-width: 360px; }
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
  .inprocess-pill                      {
    font-size: 30px;
    height: 96px;
    padding: 10px 28px 10px 10px;
    gap: 16px;
  }
  .pill-serial                         { font-size: 30px; padding-left: 12px; }
  .pill-name                           { font-size: 30px; }
  .pill-process                        { font-size: 28px; max-width: 480px; }
  .inprocess-empty                     { font-size: 30px; padding: 48px 0; }
}
</style>
