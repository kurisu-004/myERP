<template>
  <div class="dashboard">
    <!-- 主行：左侧 70% 货架分组，右侧 30% 交期分布 + 加工中 -->
    <el-row :gutter="16" class="board-row board-row-main">
      <el-col :xs="24" :lg="17">
        <el-card shadow="never" class="board-card main-left">
          <template #header>
            <div class="card-header">
              <span class="card-title"><el-icon class="card-icon"><Box /></el-icon>生产货架 <span class="card-subtitle">· 加急优先 · 交期近优先</span></span>
              <div class="header-right-tags">
                <span class="status-mini" v-if="status">
                  <span :class="['dot', status]"></span>
                  <span class="status-mini-text">{{ statusLabel }}</span>
                  <span v-if="lastUpdated" class="status-mini-time"> · {{ lastUpdated }}</span>
                </span>
                <el-tag type="warning" effect="dark">{{ totalOnShelves }}</el-tag>
              </div>
            </div>
          </template>
          <div v-if="shelfGroups.length === 0" class="empty">暂无货架上的零件</div>
          <el-scrollbar v-else class="scroll-area">
            <div v-for="group in shelfGroups" :key="group.shelf_id" class="shelf-group">
              <div class="shelf-header">
                <span class="shelf-name">{{ group.shelf_code }} · {{ group.shelf_name }}</span>
                <el-tag size="small" type="primary">{{ group.items.length }} 件</el-tag>
              </div>
              <div class="cards">
                <div v-for="item in group.items" :key="item.id" :class="['part-card', { urgent: item.is_urgent }]">
                  <div class="row1">
                    <span v-if="item.serial_no" class="serial">
                      <span :class="['serial-code', serialCodeClass(item.serial_no)]">{{ serialCode(item.serial_no) }}</span>
                      <span class="serial-num">{{ serialNum(item.serial_no) }}</span>
                    </span>
                    <span class="drawing">{{ item.drawing_no }}</span>
                    <el-tag v-if="item.is_urgent" type="danger" size="small" effect="dark" class="urgent-tag">加急</el-tag>
                  </div>
                  <div class="row2">{{ item.name }}</div>
                  <div class="row3">
                    <span class="qty">× {{ item.quantity }}</span>
                    <span class="customer">{{ item.customer_path || '-' }}</span>
                  </div>
                  <div class="row4">
                    <span class="wait-label">{{ deliveryLabel(item) }}</span>
                    <span :class="['wait-time', { overdue: isOverdue(item) }]">{{ deliveryText(item) }}</span>
                    <span class="wait-label">· 已等待</span>
                    <span class="wait-time">{{ formatDuration(now - toMs(item.placed_at)) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </el-scrollbar>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="7">
        <div class="right-stack">
          <el-card shadow="never" class="board-card delivery-card">
            <template #header>
              <div class="card-header">
                <span class="card-title"><el-icon class="card-icon"><Calendar /></el-icon>未来 7 日交期分布</span>
                <el-tag type="primary" effect="dark">{{ totalUpcoming }} 件</el-tag>
              </div>
            </template>
            <div class="delivery-chart">
              <div v-for="(bar, idx) in upcomingDelivery" :key="bar.date" :class="['bar-col', { today: idx === 0 }]">
                <div class="bar-count"><span v-if="bar.count > 0" class="bar-num">{{ bar.count }}</span></div>
                <div class="bar-track"><div class="bar-fill" :style="{ height: barHeight(bar.count) }"></div></div>
                <div class="bar-label"><div class="bar-day">{{ dayLabel(idx) }}</div><div class="bar-date">{{ formatShortDate(bar.date) }}</div></div>
              </div>
            </div>
            <div class="delivery-foot">
              <span class="foot-stat"><span class="foot-dot dot-warn"></span> 今日 {{ upcomingDelivery[0]?.count ?? 0 }} 件</span>
              <span class="foot-stat"><span class="foot-dot dot-info"></span> 后 6 日 {{ totalUpcoming - (upcomingDelivery[0]?.count ?? 0) }} 件</span>
            </div>
          </el-card>

          <!-- 品检货架 -->
          <el-card shadow="never" class="board-card insp-card" v-if="inspectionItems.length > 0">
            <template #header>
              <div class="card-header">
                <span class="card-title"><el-icon class="card-icon"><Link /></el-icon>品检中</span>
                <el-tag type="warning" effect="dark">{{ inspectionItems.length }} 件</el-tag>
              </div>
            </template>
            <div class="cards">
              <div v-for="item in inspectionItems" :key="item.id" class="part-card insp-item">
                <div class="row1"><span class="drawing">{{ item.drawing_no }}</span></div>
                <div class="row2">{{ item.name }}</div>
                <div class="row3"><span class="customer">{{ item.shelf_code || '-' }}</span></div>
              </div>
            </div>
          </el-card>

          <el-card shadow="never" class="board-card inprocess-card">
            <template #header>
              <div class="card-header">
                <span class="card-title"><el-icon class="card-icon"><Tools /></el-icon>正在加工<span class="card-subtitle">· 自动切换</span></span>
                <div class="header-right-tags">
                  <el-tag type="primary" effect="dark">{{ workerParts.length }} 件</el-tag>
                  <span class="autoplay-hint" v-if="workerParts.length > 1">{{ paused ? '已暂停' : '自动切换中' }}</span>
                </div>
              </div>
            </template>
            <div v-if="workerParts.length === 0" class="empty">暂无正在加工的零件</div>
            <el-carousel v-else :interval="5000" arrow="never" indicator-position="outside" :autoplay="workerParts.length > 1" :pause-on-hover="true" height="200px" class="process-carousel">
              <el-carousel-item v-for="(item, idx) in workerParts" :key="item.id">
                <div class="process-slide">
                  <div class="process-left">
                    <div class="row1">
                      <span v-if="item.serial_no" class="serial">
                        <span :class="['serial-code', serialCodeClass(item.serial_no)]">{{ serialCode(item.serial_no) }}</span>
                        <span class="serial-num">{{ serialNum(item.serial_no) }}</span>
                      </span>
                      <span class="drawing">{{ item.drawing_no }}</span>
                      <el-tag v-if="item.is_urgent" type="danger" size="small" effect="dark" class="urgent-tag">加急</el-tag>
                    </div>
                    <div class="process-name">{{ item.name }}</div>
                    <div class="row3">
                      <span class="qty">× {{ item.quantity }}</span>
                      <span class="customer">{{ item.customer_path || '-' }}</span>
                      <span v-if="item.planned_delivery_date" class="delivery"> · 交期 {{ formatShortDate(item.planned_delivery_date) }}</span>
                    </div>
                  </div>
                  <div class="process-right">
                    <div class="worker-avatar" :style="{ background: workerColor(item.worker_name || null) }">
                      {{ workerInitial(item.worker_name || null) }}
                    </div>
                    <div class="worker-info">
                      <div class="worker-name">{{ item.worker_name || '未记录' }}</div>
                      <div class="worker-meta">
                        <span class="wait-label">已用时</span>
                        <span class="wait-time">{{ formatDuration(now - toMs(item.picked_up_at)) }}</span>
                      </div>
                    </div>
                  </div>
                  <div class="process-foot">{{ idx + 1 }} / {{ workerParts.length }}</div>
                </div>
              </el-carousel-item>
            </el-carousel>
          </el-card>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Box, Calendar, Link, Tools } from '@element-plus/icons-vue'
import { onDashboardSnapshot, onDashboardStatus } from '@/api/dashboard'
import type { ConnectionStatus, DashboardPartItem, DashboardShelfGroup, DashboardSnapshot, UpcomingDeliveryEntry } from '@/types/dashboard'

const shelfGroups = ref<DashboardShelfGroup[]>([])
const inspectionItems = ref<DashboardPartItem[]>([])
const workerParts = ref<DashboardPartItem[]>([])
const upcomingDelivery = ref<UpcomingDeliveryEntry[]>([])
const status = ref<ConnectionStatus>('connecting')
const lastUpdated = ref('')
const now = ref<number>(Date.now())
const paused = ref(false)

let offSnap: (() => void) | null = null
let offStatus: (() => void) | null = null
let timer: ReturnType<typeof setInterval> | null = null

const statusLabel = computed(() => {
  if (status.value === 'open') return '已连接'
  if (status.value === 'connecting') return '连接中'
  return '已断开'
})
const totalOnShelves = computed(() => shelfGroups.value.reduce((sum, g) => sum + g.items.length, 0))
const totalUpcoming = computed(() => upcomingDelivery.value.reduce((sum, b) => sum + b.count, 0))

function toMs(iso: string | null | undefined): number {
  if (!iso) return now.value
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t : now.value
}
function parseYmd(s: string | null): Date | null {
  if (!s) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s)
  if (!m) return null
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}
function startOfToday(): Date { const d = new Date(); d.setHours(0, 0, 0, 0); return d }

function formatDuration(ms: number): string {
  if (ms < 0) ms = 0
  const sec = Math.floor(ms / 1000), hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`
}
function formatTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false }) } catch { return iso }
}
function formatShortDate(s: string): string { return s.length >= 10 ? s.slice(5) : s }

function serialCode(s: string): string { return s.charAt(0) }
function serialNum(s: string): string { return s.slice(1) }
function serialCodeClass(s: string): string { return `code-${serialCode(s).toLowerCase()}` }

function workerColor(name: string | null): string {
  if (!name) return '#999'
  let hash = 0; for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  return `hsl(${hash % 360}, 65%, 55%)`
}
function workerInitial(name: string | null): string {
  if (!name) return '?'
  return name.trim().charAt(0).toUpperCase()
}

function diffDays(plan: string | null): number | null {
  const d = parseYmd(plan)
  if (!d) return null
  return Math.round((d.getTime() - startOfToday().getTime()) / 86_400_000)
}
function isOverdue(item: DashboardPartItem): boolean { const d = diffDays(item.planned_delivery_date); return d !== null && d < 0 }
function deliveryLabel(item: DashboardPartItem): string {
  const d = diffDays(item.planned_delivery_date)
  if (d === null) return '交期'
  if (d < 0) return `逾期 ${-d} 天`
  if (d === 0) return '今天交期'
  if (d === 1) return '明天交期'
  return `${d} 天后交期`
}
function deliveryText(item: DashboardPartItem): string { return item.planned_delivery_date ? formatShortDate(item.planned_delivery_date) : '-' }

const maxBarCount = computed(() => upcomingDelivery.value.reduce((m, b) => Math.max(m, b.count), 0))
function barHeight(count: number): string {
  const max = maxBarCount.value
  if (max === 0 || count === 0) return '4px'
  return `${Math.max(8, Math.round((count / max) * 100))}%`
}
function dayLabel(idx: number): string { return idx === 0 ? '今天' : idx === 1 ? '明天' : `+${idx} 天` }

function applySnapshot(snap: DashboardSnapshot): void {
  shelfGroups.value = snap.data.on_production_shelves
  workerParts.value = snap.data.in_process
  inspectionItems.value = snap.data.on_inspection_shelves || []
  upcomingDelivery.value = snap.data.upcoming_delivery
  lastUpdated.value = formatTime(snap.ts)
}

onMounted(() => {
  offSnap = onDashboardSnapshot(applySnapshot)
  offStatus = onDashboardStatus((s) => { status.value = s; paused.value = s !== 'open' })
  timer = setInterval(() => { now.value = Date.now() }, 1000)
})

onBeforeUnmount(() => {
  offSnap?.(); offSnap = null
  offStatus?.(); offStatus = null
  if (timer) clearInterval(timer)
})
</script>

<style lang="scss" scoped>
.dashboard { padding: 4px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #c0c4cc; transition: all 0.2s; }
.dot.open { background: #5cb85c; box-shadow: 0 0 0 3px rgba(92, 184, 92, 0.15); }
.dot.connecting { background: #f0ad4e; }
.dot.closed { background: #d9534f; }
.board-row { margin-bottom: 0; }
.board-row-main { height: calc(100vh - 60px); }
.main-left { height: 100%; display: flex; flex-direction: column; }
.right-stack { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.delivery-card { flex: 0 0 auto; height: 30%; display: flex; flex-direction: column; }
.insp-card { flex: 0 0 auto; max-height: 120px; display: flex; flex-direction: column; }
.inprocess-card { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.board-card { display: flex; flex-direction: column; }
.board-card :deep(.el-card__body) { flex: 1; overflow: auto; padding: 12px; }
.inprocess-card :deep(.el-card__body) { overflow: hidden; padding: 8px 12px; }
.insp-card :deep(.el-card__body) { overflow: auto; padding: 8px 12px; }
.status-mini { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--text-secondary); }
.status-mini-text { font-weight: 500; }
.status-mini-time { font-family: 'SF Mono', Menlo, Consolas, monospace; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-title { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
.card-subtitle { font-weight: normal; font-size: 12px; color: var(--text-secondary); margin-left: 4px; }
.card-icon { font-size: 16px; color: var(--primary-color); }
.empty { padding: 60px 0; text-align: center; color: var(--text-secondary); font-size: 14px; }
.scroll-area { height: 100%; }
.shelf-group { margin-bottom: 16px; }
.shelf-header { display: flex; align-items: center; justify-content: space-between; padding: 6px 8px; background: var(--primary-bg); border-radius: 4px; margin-bottom: 8px; }
.shelf-name { font-size: 13px; font-weight: 600; color: var(--primary-color); }
.cards { display: flex; flex-direction: column; gap: 8px; }
.part-card { border: 1px solid var(--border-color); border-left: 4px solid var(--primary-color); border-radius: 4px; padding: 8px 12px; background: #fafbfc; transition: all 0.2s; }
.part-card.urgent { border-left-color: #d9534f; background: #fef5f4; }
.insp-item { border-left-color: #e6a23c; }
.row1 { display: flex; align-items: center; gap: 8px; }
.urgent-tag { margin-left: auto; }
.row2 { font-size: 14px; font-weight: 600; margin: 4px 0; color: var(--text-primary); }
.row3 { display: flex; gap: 12px; font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.row4 { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); flex-wrap: wrap; }
.wait-time { font-family: 'SF Mono', Menlo, Consolas, monospace; font-weight: 600; color: var(--text-primary); }
.wait-time.overdue { color: #d9534f; }
.serial { display: inline-flex; align-items: center; gap: 2px; font-family: 'SF Mono', Menlo, Consolas, monospace; font-weight: 600; }
.serial-code { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 12px; color: #fff; }
.serial-code.code-f { background: #d9534f; }
.serial-code.code-l { background: #1e4d8b; }
.serial-num { font-size: 13px; color: var(--text-primary); }
.drawing { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; color: var(--text-secondary); }
.qty { font-weight: 600; color: var(--primary-color); }
.delivery-chart { display: flex; align-items: flex-end; gap: 6px; height: calc(100% - 36px); padding: 0 4px 4px; }
.bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; min-width: 0; }
.bar-count { height: 18px; display: flex; align-items: center; justify-content: center; }
.bar-num { display: inline-block; min-width: 18px; padding: 0 5px; height: 18px; line-height: 18px; text-align: center; font-size: 11px; font-weight: 600; border-radius: 9px; background: var(--primary-bg); color: var(--primary-color); }
.bar-track { flex: 1; width: 100%; display: flex; align-items: flex-end; justify-content: center; }
.bar-fill { width: 70%; max-width: 36px; background: var(--primary-light); border-radius: 4px 4px 0 0; min-height: 4px; transition: height 0.4s ease; }
.bar-col.today .bar-fill { background: var(--primary-color); }
.bar-col.today .bar-num { background: var(--primary-color); color: #fff; }
.bar-label { margin-top: 6px; text-align: center; }
.bar-day { font-size: 12px; color: var(--text-primary); font-weight: 500; }
.bar-date { font-size: 11px; color: var(--text-secondary); font-family: 'SF Mono', Menlo, Consolas, monospace; }
.delivery-foot { display: flex; justify-content: space-around; padding: 8px 0 0; border-top: 1px dashed var(--border-color); font-size: 12px; color: var(--text-secondary); }
.foot-stat { display: inline-flex; align-items: center; gap: 4px; }
.foot-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.dot-warn { background: var(--primary-color); }
.dot-info { background: var(--primary-light); }
.process-carousel { height: 100%; }
.process-carousel :deep(.el-carousel__container) { height: calc(100% - 28px); }
.process-carousel :deep(.el-carousel__indicators--outside) { margin-top: 4px; }
.process-slide { height: 100%; display: grid; grid-template-columns: 1fr; grid-template-rows: 1fr auto; gap: 8px; padding: 8px 10px; background: linear-gradient(135deg, #fafbfc 0%, #f0f5fa 100%); border-radius: 6px; border: 1px solid var(--border-color); position: relative; }
.process-left { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.process-name { font-size: 16px; font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.process-right { display: flex; align-items: center; gap: 10px; padding: 6px 10px; background: var(--white); border-radius: 6px; border: 1px solid var(--border-color); min-height: 48px; }
.worker-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 16px; font-weight: 600; flex-shrink: 0; }
.worker-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.worker-name { font-size: 14px; font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.worker-meta { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }
.process-foot { position: absolute; bottom: 8px; right: 12px; font-size: 11px; color: var(--text-secondary); font-family: 'SF Mono', Menlo, Consolas, monospace; }
.header-right-tags { display: flex; align-items: center; gap: 8px; }
.autoplay-hint { font-size: 12px; color: var(--text-secondary); }
.delivery { color: var(--text-secondary); }
</style>
