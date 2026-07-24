import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  static instances: FakeWebSocket[] = []

  readonly url: string
  readyState = FakeWebSocket.CONNECTING
  readonly send = vi.fn()
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

async function loadDashboardApi() {
  vi.resetModules()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' })
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null) })
  vi.stubGlobal('window', { addEventListener: vi.fn() })
  return import('./dashboard')
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('dashboard WebSocket channel subscriptions', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
  })

  it('subscribes and unsubscribes the dashboard channel without closing the socket', async () => {
    const api = await loadDashboardApi()
    const off = api.onDashboardSnapshot(() => undefined)
    const socket = FakeWebSocket.instances[0]

    socket.open()
    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'subscribe', channel: 'dashboard' }),
    )

    socket.send.mockClear()
    off()

    expect(socket.readyState).toBe(FakeWebSocket.OPEN)
    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'unsubscribe', channel: 'dashboard' }),
    )
  })

  it('resubscribes a returning dashboard handler on an existing open socket', async () => {
    const api = await loadDashboardApi()
    const off = api.onDashboardSnapshot(() => undefined)
    const socket = FakeWebSocket.instances[0]
    socket.open()
    off()
    socket.send.mockClear()

    api.onDashboardSnapshot(() => undefined)

    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'subscribe', channel: 'dashboard' }),
    )
  })

  it('keeps event subscription independent from dashboard subscription', async () => {
    const api = await loadDashboardApi()
    const offEvent = api.onDashboardEvent(() => undefined)
    const socket = FakeWebSocket.instances[0]
    socket.open()

    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'subscribe', channel: 'events' }),
    )
    expect(socket.send).not.toHaveBeenCalledWith(
      JSON.stringify({ type: 'subscribe', channel: 'dashboard' }),
    )

    socket.send.mockClear()
    offEvent()
    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'unsubscribe', channel: 'events' }),
    )
  })

  it('restores active subscriptions after reconnecting', async () => {
    const api = await loadDashboardApi()
    const off = api.onDashboardSnapshot(() => undefined)
    const oldSocket = FakeWebSocket.instances[0]
    oldSocket.open()

    api.reconnectDashboard()

    const newSocket = FakeWebSocket.instances[1]
    expect(oldSocket.readyState).toBe(FakeWebSocket.CLOSED)
    newSocket.open()
    expect(newSocket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'subscribe', channel: 'dashboard' }),
    )

    off()
  })
})
