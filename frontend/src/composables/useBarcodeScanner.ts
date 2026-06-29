// composables/useBarcodeScanner.ts
//
// 全局扫码枪监听：以单例方式挂到 window.keydown；只接收"快速连击"按键序列，
// 以 Enter 结尾。识别到完整扫码后通过订阅者回调分发。
//
// 用法（在 App.vue 或具体业务页面）：
//   const { onScan, setEnabled, clearBuffer, lastScan, lastScanAt } = useBarcodeScanner()
//   onScan((code) => { ... })
//
// 设计要点：
// - 模块级单例：listener 只挂一次，避免 HMR/多次调用造成重复监听。
// - Enter 必须先判断：event.key === 'Enter' 长度是 5，被早 return 过滤掉就废了。
// - 输入框/可编辑区域不拦截：让用户在搜索、表单里正常打字，不会被误当成扫码。
// - 扫码前缀后必须 preventDefault：避免 Enter 同时触发 form submit。
// - 订阅者抛错要 try/catch：单个页面报错不应让全局监听崩。

import { onBeforeUnmount, ref } from 'vue'

export type ScanHandler = (code: string) => void
export type Unsubscribe = () => void

/** 两次按键的最大间隔；超过这个值认为是"人工打字"而非扫码枪（ms） */
const SCAN_INTERVAL_MS = 30
/** 缓冲区最大长度；超过则丢弃，防止异常状态下无限累积 */
const SCAN_MAX_LENGTH = 50
/** 缓冲区空闲超时（ms）；超过则清空（兜底，正常扫码枪会带 Enter 结束） */
const BUFFER_IDLE_MS = 500

// ============ 单例状态（模块级） ============
const handlers = new Set<ScanHandler>()
const enabled = ref(true)
const lastScan = ref<string>('')
const lastScanAt = ref<number>(0)
let codeBuffer = ''
let lastTime = 0
let idleTimer: ReturnType<typeof setTimeout> | null = null
let listenerInstalled = false

function isInTextField(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (target.isContentEditable) return true
  return false
}

function resetBuffer(): void {
  codeBuffer = ''
  lastTime = 0
  if (idleTimer !== null) {
    clearTimeout(idleTimer)
    idleTimer = null
  }
}

function scheduleIdleClear(): void {
  if (idleTimer !== null) clearTimeout(idleTimer)
  idleTimer = setTimeout(() => {
    resetBuffer()
  }, BUFFER_IDLE_MS)
}

function dispatch(code: string): void {
  if (!code) return
  lastScan.value = code
  lastScanAt.value = Date.now()
  // eslint-disable-next-line no-console
  console.log(`[barcode] ${code}`)
  for (const h of handlers) {
    try {
      h(code)
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[barcode] handler threw', e)
    }
  }
}

function handleKeyDown(event: KeyboardEvent): void {
  if (!enabled.value) return

  // 1) Enter 必须先判断 —— key 长度 5，会被后面的 length>1 过滤掉
  if (event.key === 'Enter') {
    if (codeBuffer.length > 0) {
      const code = codeBuffer
      resetBuffer()
      // 阻止默认行为：避免 Enter 同时提交表单/插入换行
      event.preventDefault()
      dispatch(code)
    }
    return
  }

  // 2) 用户正在输入框/可编辑区域打字，不拦截
  if (isInTextField(event.target)) return

  // 3) 其他特殊键（Shift/Ctrl/方向键等）忽略
  if (event.key.length > 1) return

  // 4) 时间窗判定
  const now = Date.now()
  if (lastTime === 0 || now - lastTime < SCAN_INTERVAL_MS) {
    codeBuffer += event.key
  } else {
    // 间隔太长，认为是新一轮输入（或误触）
    codeBuffer = event.key
  }
  lastTime = now
  scheduleIdleClear()

  // 5) 超长保护
  if (codeBuffer.length > SCAN_MAX_LENGTH) {
    codeBuffer = ''
  }
}

function installListener(): void {
  if (listenerInstalled) return
  if (typeof window === 'undefined') return
  window.addEventListener('keydown', handleKeyDown)
  listenerInstalled = true
}

// ============ 公开 API ============
export function useBarcodeScanner() {
  installListener()

  function onScan(handler: ScanHandler): Unsubscribe {
    handlers.add(handler)
    return () => {
      handlers.delete(handler)
    }
  }

  function setEnabled(value: boolean): void {
    enabled.value = value
  }

  function clearBuffer(): void {
    resetBuffer()
  }

  // HMR 友好：在每次调用时挂个 onBeforeUnmount，没实际副作用，
  // 主要为了让 useBarcodeScanner 仍按"composable 规则"出现在 setup() 里。
  onBeforeUnmount(() => {
    /* listener 跟随模块单例，不随组件卸载 */
  })

  return {
    onScan,
    setEnabled,
    clearBuffer,
    enabled,
    lastScan,
    lastScanAt,
  }
}
