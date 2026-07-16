// 大屏 WebSocket：单例订阅式客户端。
//
// 用法：
//   const off = onDashboardSnapshot(snap => ...)
//   onDashboardEvent(ev => ...)
//   onDashboardStatus(s => ...)
//   // 组件卸载时调 off() 反订阅；模块本身会保持长连接并在断线时自动重连。

import type {
  ConnectionStatus,
  DashboardEvent,
  DashboardServerMessage,
  DashboardSnapshot,
} from '@/types/dashboard'

type SnapshotHandler = (snap: DashboardSnapshot) => void
type EventHandler = (ev: DashboardEvent) => void
type StatusHandler = (status: ConnectionStatus) => void

// —— 模块级单例状态 ——
let ws: WebSocket | null = null
let closed = false
let retryTimer: ReturnType<typeof setTimeout> | null = null
let retryDelay = 1000

const snapSubs = new Set<SnapshotHandler>()
const eventSubs = new Set<EventHandler>()
const statusSubs = new Set<StatusHandler>()

function url(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const base = `${proto}://${location.host}/api/v1/ws/dashboard`
  const raw = localStorage.getItem('auth_session')
  if (raw) {
    try {
      const s = JSON.parse(raw)
      if (s.token) return `${base}?token=${encodeURIComponent(s.token)}`
    } catch { /* ignore */ }
  }
  return base
}

function notifyStatus(s: ConnectionStatus): void {
  for (const h of statusSubs) {
    try {
      h(s)
    } catch (e) {
      console.error('dashboard status handler error', e)
    }
  }
}

function dispatch(msg: DashboardServerMessage): void {
  if (msg.type === 'snapshot') {
    for (const h of snapSubs) {
      try {
        h(msg)
      } catch (e) {
        console.error('dashboard snapshot handler error', e)
      }
    }
  } else if (msg.type === 'event') {
    for (const h of eventSubs) {
      try {
        h(msg)
      } catch (e) {
        console.error('dashboard event handler error', e)
      }
    }
  }
}

function connect(): void {
  if (closed) return
  notifyStatus('connecting')
  ws = new WebSocket(url())
  ws.onopen = () => {
    retryDelay = 1000
    notifyStatus('open')
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data) as DashboardServerMessage
      dispatch(msg)
    } catch (e) {
      console.error('dashboard WS parse error', e)
    }
  }
  ws.onerror = () => {
    // onclose 紧随其后
  }
  ws.onclose = () => {
    notifyStatus('closed')
    ws = null
    if (closed) return
    retryTimer = setTimeout(connect, retryDelay)
    retryDelay = Math.min(retryDelay * 2, 10000)
  }
}

function ensureConnected(): void {
  if (ws || closed) return
  connect()
}

// ============================================================
// 公共 API：订阅 / 反订阅
// ============================================================

/** 订阅 snapshot 推送；返回反订阅函数。首次调用即触发建立连接。 */
export function onDashboardSnapshot(h: SnapshotHandler): () => void {
  snapSubs.add(h)
  ensureConnected()
  return () => snapSubs.delete(h)
}

/** 订阅业务事件（PICKED_UP / RELEASED），由横幅通知组件消费。 */
export function onDashboardEvent(h: EventHandler): () => void {
  eventSubs.add(h)
  ensureConnected()
  return () => eventSubs.delete(h)
}

/** 订阅连接状态变化。 */
export function onDashboardStatus(h: StatusHandler): () => void {
  statusSubs.add(h)
  ensureConnected()
  return () => statusSubs.delete(h)
}

/** 显式关闭长连接（一般不调用，保留供登出/测试使用）。 */
export function closeDashboard(): void {
  closed = true
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = null
  ws?.close()
  ws = null
  snapSubs.clear()
  eventSubs.clear()
  statusSubs.clear()
}

/** 强制发起一次重连（修「点首页不能自动恢复连接」bug）。
 *
 * 行为：
 *   1. 清掉 `retryTimer` / `closed = false` / `retryDelay` 归 1s；
 *   2. 若 ws 已存在，CLOSING/OPEN 状态主动 close 后置 null；
 *   3. 立即 `connect()`。
 *
 * Router afterEach 在 `to.name === 'Dashboard'` 时调用本函数，确保用户
 * 每次回到首页都能恢复连接——即便之前因 onclose 后退避停留在 10s 状态。
 */
export function reconnectDashboard(): void {
  closed = false
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  retryDelay = 1000
  if (ws) {
    const state = ws.readyState
    // OPEN / CLOSING (1 / 2) 主动关；CONNECTING (0) / CLOSED (3) 直接置 null。
    // CONNECTING 时关 close 会触发 onerror 链，无意义。
    if (state === WebSocket.OPEN || state === WebSocket.CLOSING) {
      try { ws.close() } catch { /* ignore */ }
    }
    ws = null
  }
  connect()
}

// —— JWT 自动刷新后顺势重连，避免陈旧 token 卡住 socket ——
if (typeof window !== 'undefined') {
  window.addEventListener('auth:tokens-refreshed', () => {
    // url() 内每次现读 localStorage，新 token 已就位；强制 socket 切到新握手。
    reconnectDashboard()
  })
}