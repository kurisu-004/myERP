// 大屏 WebSocket 客户端。连接、解析、失败自动重连。

/** 大屏 ready_queue / in_process 共用的最小数据项 */
export interface DashboardPartItem {
  id: number
  serial_no: string | null
  name: string
  drawing_no: string
  quantity: number
  planned_delivery_date: string | null
  released_at: string | null
  picked_up_at: string | null
  current_worker_id: number | null
  worker_name: string | null
  customer_name: string | null
  customer_path: string | null
}

export interface DashboardSnapshotData {
  ready_queue: DashboardPartItem[]
  in_process: DashboardPartItem[]
  ts: string
}

export interface DashboardSnapshot {
  type: 'snapshot'
  data: DashboardSnapshotData
  ts: string
}

export type ConnectionStatus = 'connecting' | 'open' | 'closed'

export interface DashboardClient {
  close: () => void
}

export function connectDashboard(
  onSnapshot: (snap: DashboardSnapshot) => void,
  onStatus: (status: ConnectionStatus) => void,
): DashboardClient {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/api/v1/ws/dashboard`

  let ws: WebSocket | null = null
  let closed = false
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryDelay = 1000

  function connect() {
    if (closed) return
    onStatus('connecting')
    ws = new WebSocket(url)
    ws.onopen = () => {
      retryDelay = 1000
      onStatus('open')
    }
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as DashboardSnapshot
        if (msg.type === 'snapshot') onSnapshot(msg)
      } catch (e) {
        console.error('dashboard WS parse error', e)
      }
    }
    ws.onerror = () => {
      // onclose 会跟着触发
    }
    ws.onclose = () => {
      onStatus('closed')
      if (closed) return
      retryTimer = setTimeout(connect, retryDelay)
      retryDelay = Math.min(retryDelay * 2, 10000)
    }
  }
  connect()

  return {
    close() {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    },
  }
}