import { ref, type Ref } from 'vue'

export type ScrollDir = 'up' | 'down'

export function useHoldToScroll(containerRef: Ref<HTMLElement | null>) {
  const atTop = ref(true)
  const atBottom = ref(true)

  let rafId: number | null = null
  let holdTimer: ReturnType<typeof setTimeout> | null = null
  let pointerDownTime = 0
  let currentDir: ScrollDir | null = null
  let isPressed = false
  let hasStartedLongScroll = false
  let recalcTimer: ReturnType<typeof setTimeout> | null = null

  function recalc(): void {
    if (recalcTimer) {
      clearTimeout(recalcTimer)
    }
    recalcTimer = setTimeout(() => {
      const el = containerRef.value
      if (!el) {
        atTop.value = true
        atBottom.value = true
        return
      }
      const { scrollTop, clientHeight, scrollHeight } = el
      // 内容不足一页时，两个都禁用
      if (scrollHeight <= clientHeight + 1) {
        atTop.value = true
        atBottom.value = true
        return
      }
      atTop.value = scrollTop <= 1
      atBottom.value = scrollTop + clientHeight >= scrollHeight - 1
    }, 50)
  }

  function stopScroll(): void {
    isPressed = false
    currentDir = null
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
    if (holdTimer) {
      clearTimeout(holdTimer)
      holdTimer = null
    }
  }

  function onPressDown(dir: ScrollDir): void {
    stopScroll()
    pointerDownTime = Date.now()
    currentDir = dir
    isPressed = true
    hasStartedLongScroll = false

    holdTimer = setTimeout(() => {
      if (!isPressed || currentDir !== dir) return
      hasStartedLongScroll = true
      const rafStart = performance.now()

      function frame(ts: number): void {
        if (!isPressed || currentDir !== dir) return
        const elapsed = ts - rafStart
        const base = dir === 'up' ? -1 : 1
        const speed = base * Math.min(12, 4 + elapsed / 60)
        const el = containerRef.value
        if (el) el.scrollBy({ top: speed, behavior: 'auto' })
        rafId = requestAnimationFrame(frame)
      }

      rafId = requestAnimationFrame(frame)
    }, 250)
  }

  function onPressUp(): void {
    const duration = Date.now() - pointerDownTime
    const dir = currentDir
    const didLongScroll = hasStartedLongScroll

    stopScroll()
    hasStartedLongScroll = false

    if (!didLongScroll && duration < 300 && dir) {
      const el = containerRef.value
      if (!el) return
      const distance = Math.round(el.clientHeight * 0.6) * (dir === 'up' ? -1 : 1)
      el.scrollBy({ top: distance, behavior: 'smooth' })
    }
  }

  let ro: ResizeObserver | null = null
  let mo: MutationObserver | null = null

  function bindContainer(): void {
    const el = containerRef.value
    if (!el) return

    unbindContainer()

    el.addEventListener('scroll', recalc, { passive: true })

    ro = new ResizeObserver(() => recalc())
    ro.observe(el)

    mo = new MutationObserver(() => recalc())
    mo.observe(el, { childList: true, subtree: true })

    recalc()
  }

  function unbindContainer(): void {
    const el = containerRef.value
    if (el) {
      el.removeEventListener('scroll', recalc)
    }
    if (ro) {
      ro.disconnect()
      ro = null
    }
    if (mo) {
      mo.disconnect()
      mo = null
    }
    stopScroll()
  }

  return {
    atTop,
    atBottom,
    recalc,
    onPressDown,
    onPressUp,
    bindContainer,
    unbindContainer,
  }
}
