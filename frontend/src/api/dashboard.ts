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

function teardown(socket: WebSocket): void {
  // 拆掉旧 socket 的所有回调后再 close，确保它的 onclose 不会回过头来
  // 清掉刚建立的新 socket / 触发多余重连（多连接堆叠 → 大屏收到重复推送）。
  socket.onopen = null
  socket.onmessage = null
  socket.onerror = null
  socket.onclose = null
  try {
    socket.close()
  } catch { /* ignore */ }
}

function connect(): void {
  if (closed) return
  // 旧 socket 还在握手（CONNECTING）期间不允许 teardown——close() 会让浏览器报
  // "WebSocket is closed before the connection is established"。所有调用方（首次
  // 进入 Dashboard 时 onMounted 与 router.afterEach 同 tick 双触发；JWT 刷新后
  // reconnectDashboard）共用同一单例 URL，等这次握手完成即可，无需 close。
  if (ws && ws.readyState === WebSocket.CONNECTING) return
  // 保证同一时刻只有一条活连接：建新连接前先拆掉旧的。
  if (ws) {
    teardown(ws)
    ws = null
  }
  notifyStatus('connecting')
  const socket = new WebSocket(url())
  ws = socket
  socket.onopen = () => {
    if (socket !== ws) return
    retryDelay = 1000
    notifyStatus('open')
  }
  socket.onmessage = (ev) => {
    if (socket !== ws) return
    try {
      const msg = JSON.parse(ev.data) as DashboardServerMessage
      dispatch(msg)
    } catch (e) {
      console.error('dashboard WS parse error', e)
    }
  }
  socket.onerror = () => {
    // onclose 紧随其后
  }
  socket.onclose = () => {
    // 陈旧 socket（已被新连接取代）的 onclose 直接忽略，避免误清新 ws / 误重连。
    if (socket !== ws) return
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
 * 行为：清 `retryTimer` / `closed = false` / `retryDelay` 归 1s，然后 `connect()`。
 * 关旧 socket 的活交给 `connect()`（内部先 `teardown(ws)` 再建新连接），
 * 保证任何时刻只有一条活连接——避免旧 socket 的 onclose 误清新 ws / 误重连
 * 导致同一浏览器堆叠多条连接、大屏收到重复推送。
 *
 * Router afterEach 在 `to.name === 'Dashboard'` 时调用本函数，确保用户
 * 每次回到首页都能恢复连接——即便之前因 onclose 后退避停留在 10s 状态。
 */
export function reconnectDashboard(): void {
  closed = false
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  retryDelay = 1000
  connect()
}

// —— JWT 自动刷新后顺势重连，避免陈旧 token 卡住 socket ——
// 用 module-level flag 保证只注册一次监听（HMR 下模块可能被重复求值）。
let refreshListenerBound = false
if (typeof window !== 'undefined' && !refreshListenerBound) {
  refreshListenerBound = true
  window.addEventListener('auth:tokens-refreshed', () => {
    // url() 内每次现读 localStorage，新 token 已就位；强制 socket 切到新握手。
    reconnectDashboard()
  })
}