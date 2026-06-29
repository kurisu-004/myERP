<template>
  <div class="dashboard">
    <el-card class="header-card" shadow="never">
      <div class="header">
        <div class="title">
          <span class="title-text">车间生产大屏</span>
          <span class="subtitle">实时待加工队列 · 正在加工工人</span>
        </div>
        <div class="status">
          <span :class="['dot', status]"></span>
          <span class="status-text">{{ statusLabel }}</span>
          <span class="last-updated" v-if="lastUpdated">· 上次更新 {{ lastUpdated }}</span>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" class="board">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="board-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">
                <el-icon class="card-icon"><Box /></el-icon>
                待加工队列
              </span>
              <el-tag type="warning" effect="dark">{{ readyQueue.length }}</el-tag>
            </div>
          </template>
          <div v-if="readyQueue.length === 0" class="empty">暂无待加工零件</div>
          <div v-else class="cards">
            <div
              v-for="item in readyQueue"
              :key="item.id"
              class="part-card"
            >
              <div class="row1">
                <span v-if="item.serial_no" class="serial">
                  <span :class="['serial-code', serialCodeClass(item.serial_no)]">
                    {{ serialCode(item.serial_no) }}
                  </span>
                  <span class="serial-num">{{ serialNum(item.serial_no) }}</span>
                </span>
                <span class="drawing">{{ item.drawing_no }}</span>
              </div>
              <div class="row2">{{ item.name }}</div>
              <div class="row3">
                <span class="qty">× {{ item.quantity }}</span>
                <span class="customer">{{ item.customer_path || '-' }}</span>
              </div>
              <div class="row4">
                <span class="wait-label">已等待</span>
                <span class="wait-time">{{ formatDuration(now - toMs(item.released_at)) }}</span>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="board-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">
                <el-icon class="card-icon"><Tools /></el-icon>
                正在加工
              </span>
              <el-tag type="primary" effect="dark">{{ inProcess.length }}</el-tag>
            </div>
          </template>
          <div v-if="inProcess.length === 0" class="empty">暂无正在加工的零件</div>
          <div v-else class="cards">
            <div
              v-for="item in inProcess"
              :key="item.id"
              class="part-card"
              :style="{ borderLeftColor: workerColor(item.worker_name) }"
            >
              <div class="row1">
                <span v-if="item.serial_no" class="serial">
                  <span :class="['serial-code', serialCodeClass(item.serial_no)]">
                    {{ serialCode(item.serial_no) }}
                  </span>
                  <span class="serial-num">{{ serialNum(item.serial_no) }}</span>
                </span>
                <span class="drawing">{{ item.drawing_no }}</span>
              </div>
              <div class="row2">{{ item.name }}</div>
              <div class="row3">
                <span class="qty">× {{ item.quantity }}</span>
                <span class="customer">{{ item.customer_path || '-' }}</span>
              </div>
              <div class="row4 worker-row">
                <el-icon><User /></el-icon>
                <span class="worker-name">{{ item.worker_name || '(未记录)' }}</span>
                <span class="wait-label">· 已用时</span>
                <span class="wait-time">{{ formatDuration(now - toMs(item.picked_up_at)) }}</span>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Box, Tools, User } from '@element-plus/icons-vue'
import { connectDashboard, type DashboardPartItem } from '@/api/dashboard'

const readyQueue = ref<DashboardPartItem[]>([])
const inProcess = ref<DashboardPartItem[]>([])
const status = ref<'connecting' | 'open' | 'closed'>('connecting')
const lastUpdated = ref<string>('')
const now = ref<number>(Date.now())
let client: { close: () => void } | null = null
let timer: ReturnType<typeof setInterval> | null = null

const statusLabel = computed(() => {
  if (status.value === 'open') return '已连接'
  if (status.value === 'connecting') return '连接中'
  return '已断开'
})

function toMs(iso: string | null): number {
  if (!iso) return now.value
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t : now.value
}

function formatDuration(ms: number): string {
  if (ms < 0) ms = 0
  const sec = Math.floor(ms / 1000)
  const hh = Math.floor(sec / 3600)
  const mm = Math.floor((sec % 3600) / 60)
  const ss = sec % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  if (hh > 0) return `${pad(hh)}:${pad(mm)}:${pad(ss)}`
  return `${pad(mm)}:${pad(ss)}`
}

function serialCode(s: string): string {
  return s.charAt(0)
}
function serialNum(s: string): string {
  return s.slice(1)
}
function serialCodeClass(s: string): string {
  return `code-${serialCode(s).toLowerCase()}`
}

// 用 worker_name 哈希出一个稳定的颜色，便于一眼区分不同工人
function workerColor(name: string | null): string {
  if (!name) return '#999'
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  const hue = hash % 360
  return `hsl(${hue}, 65%, 55%)`
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

onMounted(() => {
  client = connectDashboard(
    (snap) => {
      readyQueue.value = snap.data.ready_queue
      inProcess.value = snap.data.in_process
      lastUpdated.value = formatTime(snap.ts)
    },
    (s) => {
      status.value = s
    },
  )
  // 每秒刷新"已等待/已用时"
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  client?.close()
  if (timer) clearInterval(timer)
})
</script>

<style lang="scss" scoped>
.dashboard { padding: 4px; }
.header-card { margin-bottom: 16px; }
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.title { display: flex; align-items: baseline; gap: 12px; }
.title-text { font-size: 18px; font-weight: 600; color: var(--text-primary); }
.subtitle { font-size: 13px; color: var(--text-secondary); }

.status { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-secondary); }
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c0c4cc;
  transition: all 0.2s;
}
.dot.open { background: #5cb85c; box-shadow: 0 0 0 3px rgba(92, 184, 92, 0.15); }
.dot.connecting { background: #f0ad4e; }
.dot.closed { background: #d9534f; }

.board-card { height: calc(100vh - 160px); display: flex; flex-direction: column; }
.board-card :deep(.el-card__body) { flex: 1; overflow: auto; padding: 12px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-title { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
.card-icon { font-size: 16px; color: var(--primary-color); }

.empty {
  padding: 60px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 14px;
}

.cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.part-card {
  border: 1px solid var(--border-color);
  border-left: 4px solid var(--primary-color);
  border-radius: 4px;
  padding: 8px 12px;
  background: #fafbfc;
}
.row1 { display: flex; justify-content: space-between; align-items: center; }
.row2 { font-size: 14px; font-weight: 600; margin: 4px 0; color: var(--text-primary); }
.row3 { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.row4 { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }
.worker-row .worker-name { color: var(--primary-color); font-weight: 600; }
.wait-label { color: var(--text-secondary); }
.wait-time {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--text-primary);
}

.serial { display: inline-flex; align-items: center; gap: 2px; font-family: 'SF Mono', Menlo, Consolas, monospace; font-weight: 600; }
.serial-code { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 12px; color: #fff; }
.serial-code.code-f { background: #d9534f; }
.serial-code.code-l { background: #1e4d8b; }
.serial-num { font-size: 13px; color: var(--text-primary); }

.drawing { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 12px; color: var(--text-secondary); }
.qty { font-weight: 600; color: var(--primary-color); }
</style>