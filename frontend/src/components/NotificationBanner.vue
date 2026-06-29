<template>
  <Teleport to="body">
    <div class="notification-stack" aria-live="polite">
      <transition-group name="banner">
        <div
          v-for="item in items"
          :key="item.key"
          :class="['banner', `banner-${item.event_type.toLowerCase()}`]"
          @mouseenter="pauseTimer(item)"
          @mouseleave="resumeTimer(item)"
        >
          <el-icon class="banner-icon" :size="20">
            <Promotion v-if="item.event_type === 'PICKED_UP'" />
            <Box v-else />
          </el-icon>
          <div class="banner-body">
            <div class="banner-title">
              <span class="banner-title-text">{{ titleFor(item) }}</span>
              <el-tag
                v-if="item.is_urgent"
                type="danger"
                size="small"
                effect="dark"
                class="urgent-tag"
              >
                加急
              </el-tag>
            </div>
            <div class="banner-meta">
              <span v-if="item.drawing_no" class="drawing">{{ item.drawing_no }}</span>
              <span v-if="item.customer_path" class="customer">{{ item.customer_path }}</span>
              <span v-if="item.planned_delivery_date" class="delivery">
                交期 {{ formatDate(item.planned_delivery_date) }}
              </span>
            </div>
          </div>
          <el-button
            link
            class="banner-close"
            @click="dismiss(item.key)"
          >
            <el-icon :size="14"><Close /></el-icon>
          </el-button>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Box, Close, Promotion } from '@element-plus/icons-vue'
import { onDashboardEvent } from '@/api/dashboard'
import type {
  DashboardEvent,
  DashboardEventPayload,
  DashboardEventType,
} from '@/types/dashboard'

interface BannerItem {
  key: number
  event_type: DashboardEventType
  drawing_no: string
  name: string
  customer_path: string | null
  is_urgent: boolean
  planned_delivery_date: string | null
  worker_name: string | null
}

const MAX_VISIBLE = 3
const AUTO_DISMISS_MS = 4000

const items = ref<BannerItem[]>([])
const timers = new Map<number, ReturnType<typeof setTimeout>>()
let seq = 0
let offEvent: (() => void) | null = null

function scheduleDismiss(key: number): void {
  clearTimer(key)
  const t = setTimeout(() => dismiss(key), AUTO_DISMISS_MS)
  timers.set(key, t)
}

function clearTimer(key: number): void {
  const t = timers.get(key)
  if (t) {
    clearTimeout(t)
    timers.delete(key)
  }
}

function pauseTimer(item: BannerItem): void {
  clearTimer(item.key)
}

function resumeTimer(item: BannerItem): void {
  // 仅当仍在队列里才重置
  if (items.value.some((i) => i.key === item.key)) {
    scheduleDismiss(item.key)
  }
}

function dismiss(key: number): void {
  clearTimer(key)
  items.value = items.value.filter((i) => i.key !== key)
}

function push(ev: DashboardEvent): void {
  const key = ++seq
  const item: BannerItem = {
    key,
    event_type: ev.event_type,
    drawing_no: ev.data.drawing_no,
    name: ev.data.name,
    customer_path: ev.data.customer_path,
    is_urgent: ev.data.is_urgent,
    planned_delivery_date: ev.data.planned_delivery_date,
    worker_name: ev.data.worker_name ?? null,
  }
  // 新事件插到队首；同时只保留最新 MAX_VISIBLE 条
  items.value = [item, ...items.value].slice(0, MAX_VISIBLE)
  scheduleDismiss(key)
}

function titleFor(item: BannerItem): string {
  const who = item.worker_name || '未知工人'
  const subject = `${item.drawing_no} · ${item.name}`
  if (item.event_type === 'PICKED_UP') {
    return `${who} 领取了 ${subject}`
  }
  // RELEASED
  return `${subject} 已进入货架等待加工`
}

function formatDate(s: string): string {
  // s = "YYYY-MM-DD"；去掉年份即可
  return s.length >= 10 ? s.slice(5) : s
}

onMounted(() => {
  offEvent = onDashboardEvent(push)
})

onBeforeUnmount(() => {
  offEvent?.()
  offEvent = null
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
})
</script>

<style lang="scss" scoped>
.notification-stack {
  position: fixed;
  top: 76px;        // 顶部 header(60) + 16 间距
  right: 24px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none; // 容器不挡操作
  max-width: 360px;
}

.banner {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  background: var(--white);
  border: 1px solid var(--border-color);
  border-left: 4px solid var(--primary-color);
  border-radius: 6px;
  box-shadow: var(--shadow-md);
  min-width: 280px;
}

.banner-picked_up { border-left-color: var(--primary-color); }
.banner-picked_up .banner-icon { color: var(--primary-color); }
.banner-released  { border-left-color: #e6a23c; }
.banner-released  .banner-icon { color: #e6a23c; }

.banner-icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.banner-body {
  flex: 1;
  min-width: 0;
}

.banner-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.4;
}

.banner-title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.urgent-tag {
  flex-shrink: 0;
}

.banner-meta {
  margin-top: 4px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.banner-meta .drawing {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: var(--text-regular);
}

.banner-close {
  flex-shrink: 0;
  padding: 2px;
  color: var(--text-secondary);
}

.banner-close:hover {
  color: var(--text-primary);
}

// 入场/出场动画
.banner-enter-active,
.banner-leave-active {
  transition: all 0.25s ease;
}
.banner-enter-from {
  opacity: 0;
  transform: translateX(20px);
}
.banner-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
.banner-move {
  transition: transform 0.25s ease;
}
</style>