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
            <Upload v-else-if="item.event_type === 'RETURNED'" />
            <Link v-else-if="item.event_type === 'INSPECTED'" />
            <Box v-else />
          </el-icon>
          <div class="banner-body">
            <div class="banner-title">
              <span class="banner-title-text">{{ titleFor(item) }}</span>
              <el-tag v-if="item.is_urgent" type="danger" size="small" effect="dark" class="urgent-tag">加急</el-tag>
            </div>
            <div class="banner-meta">
              <span v-if="item.drawing_no" class="drawing">{{ item.drawing_no }}</span>
              <span v-if="item.shelf_code" class="shelf">{{ item.shelf_code }}</span>
              <span v-if="item.customer_path" class="customer">{{ item.customer_path }}</span>
              <span v-if="item.planned_delivery_date" class="delivery">交期 {{ formatDate(item.planned_delivery_date) }}</span>
            </div>
          </div>
          <el-button link class="banner-close" @click="dismiss(item.key)"><el-icon :size="14"><Close /></el-icon></el-button>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Box, Close, Link, Promotion, Upload } from '@element-plus/icons-vue'
import { onDashboardEvent } from '@/api/dashboard'
import type { DashboardEvent, DashboardEventType } from '@/types/dashboard'

interface BannerItem {
  key: number
  event_type: DashboardEventType
  drawing_no: string
  name: string
  customer_path: string | null
  is_urgent: boolean
  planned_delivery_date: string | null
  worker_name: string | null
  shelf_code?: string | null
}

const MAX_VISIBLE = 3
const AUTO_DISMISS_MS = 4000

const items = ref<BannerItem[]>([])
const timers = new Map<number, ReturnType<typeof setTimeout>>()
let seq = 0
let offEvent: (() => void) | null = null

function scheduleDismiss(key: number): void { clearTimer(key); timers.set(key, setTimeout(() => dismiss(key), AUTO_DISMISS_MS)) }
function clearTimer(key: number): void { const t = timers.get(key); if (t) { clearTimeout(t); timers.delete(key) } }
function pauseTimer(item: BannerItem): void { clearTimer(item.key) }
function resumeTimer(item: BannerItem): void { if (items.value.some((i) => i.key === item.key)) scheduleDismiss(item.key) }
function dismiss(key: number): void { clearTimer(key); items.value = items.value.filter((i) => i.key !== key) }

function push(ev: DashboardEvent): void {
  const key = ++seq
  const item: BannerItem = {
    key,
    event_type: ev.event_type,
    drawing_no: ev.data.drawing_no ?? '',
    name: ev.data.name ?? '',
    customer_path: ev.data.customer_path ?? null,
    is_urgent: ev.data.is_urgent ?? false,
    planned_delivery_date: ev.data.planned_delivery_date ?? null,
    worker_name: ev.data.worker_name ?? null,
    shelf_code: ev.data.shelf_code ?? null,
  }
  items.value = [item, ...items.value].slice(0, MAX_VISIBLE)
  scheduleDismiss(key)
}

function titleFor(item: BannerItem): string {
  const who = item.worker_name || '未知工人'
  const subject = `${item.drawing_no} · ${item.name}`
  if (item.event_type === 'PICKED_UP') return `${who} 领取了 ${subject}`
  if (item.event_type === 'RETURNED') return `${who} 归还了 ${subject}`
  if (item.event_type === 'INSPECTED') return `${who} 送了 ${subject} 去品检`
  if (item.event_type === 'PLACED_ON_SHELF') {
    const shelf = item.shelf_code || '未知货架'
    return `${subject} 已放置到 ${shelf} 等待加工`
  }
  if (item.event_type === 'RECALLED') {
    // 2026-08-05 召回：ON_SHELF/PROGRAMMING → PENDING/PROGRAMMING
    return `${subject} 已被召回`
  }
  if (item.event_type === 'ASSEMBLY_CANCELLED') {
    return `装配体 ${item.drawing_no || ''} 已取消`
  }
  if (item.event_type === 'ASSEMBLY_DELETED') {
    return `装配体 ${item.drawing_no || ''} 已删除`
  }
  // RELEASED (legacy)
  return `${subject} 已进入货架等待加工`
}

function formatDate(s: string): string { return s.length >= 10 ? s.slice(5) : s }

onMounted(() => { offEvent = onDashboardEvent(push) })
onBeforeUnmount(() => { offEvent?.(); offEvent = null; for (const t of timers.values()) clearTimeout(t); timers.clear() })
</script>

<style lang="scss" scoped>
.notification-stack { position: fixed; top: 76px; right: 24px; z-index: 2000; display: flex; flex-direction: column; gap: 8px; pointer-events: none; max-width: 400px; }
/* 手机：改为顶部通栏，避开 60px 顶栏 + iOS 安全区 */
@include until(md) {
  .notification-stack {
    top: calc(env(safe-area-inset-top, 0px) + 64px);
    left: 8px;
    right: 8px;
    max-width: none;
  }
}
.banner { pointer-events: auto; display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; background: var(--white); border: 1px solid var(--border-color); border-left: 4px solid var(--primary-color); border-radius: 6px; box-shadow: var(--shadow-md); min-width: 300px; }
@include until(md) {
  .banner { min-width: 0; }
}
.banner-picked_up { border-left-color: var(--primary-color); }
.banner-picked_up .banner-icon { color: var(--primary-color); }
.banner-placed_on_shelf { border-left-color: #e6a23c; }
.banner-placed_on_shelf .banner-icon { color: #e6a23c; }
.banner-released { border-left-color: #e6a23c; }
.banner-released .banner-icon { color: #e6a23c; }
.banner-returned { border-left-color: #67c23a; }
.banner-returned .banner-icon { color: #67c23a; }
.banner-inspected { border-left-color: #409eff; }
.banner-inspected .banner-icon { color: #409eff; }
/* 2026-08-05 召回 */
.banner-recalled { border-left-color: #f56c6c; }
.banner-recalled .banner-icon { color: #f56c6c; }
.banner-icon { flex-shrink: 0; margin-top: 2px; }
.banner-body { flex: 1; min-width: 0; }
.banner-title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; color: var(--text-primary); line-height: 1.4; }
.banner-title-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.urgent-tag { flex-shrink: 0; }
.banner-meta { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--text-secondary); }
.banner-meta .drawing { font-family: 'SF Mono', Menlo, Consolas, monospace; color: var(--text-regular); }
.banner-meta .shelf { color: #e6a23c; font-weight: 500; }
.banner-close { flex-shrink: 0; padding: 2px; color: var(--text-secondary); }
.banner-close:hover { color: var(--text-primary); }
.banner-enter-active, .banner-leave-active { transition: all 0.25s ease; }
.banner-enter-from { opacity: 0; transform: translateX(20px); }
.banner-leave-to { opacity: 0; transform: translateX(20px); }
.banner-move { transition: transform 0.25s ease; }
</style>
