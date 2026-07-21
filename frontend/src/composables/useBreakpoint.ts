import { computed, onScopeDispose, ref, type ComputedRef, type Ref } from 'vue'

/**
 * 响应式断点（手机优先 mobile-first）。
 *
 * 断点值必须与 src/styles/_breakpoints.scss 中的 $bp-* 保持同步。
 *
 * | name | min-width | 典型设备                 |
 * |------|-----------|--------------------------|
 * | xs   | 0         | 手机竖屏 (<480)          |
 * | sm   | 480       | 大屏手机 / 小平板竖屏    |
 * | md   | 768       | 平板竖屏 / 小笔记本      |
 * | lg   | 1024      | 笔记本 / 平板横屏        |
 * | xl   | 1280      | 桌面                     |
 * | 2xl  | 1536      | 宽屏桌面                 |
 *
 * 边界约定：md(≥768) 为布局边界（侧栏 aside ↔ drawer）；
 *          sm(≥480) 为内容密度边界（对话框全屏 / 表格→卡片 / 列数收窄）。
 */

export type BpName = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl'

export const BREAKPOINTS: Record<Exclude<BpName, 'xs'>, number> = {
  sm: 480,
  md: 768,
  lg: 1024,
  xl: 1280,
  '2xl': 1536,
}

function nameForWidth(width: number): BpName {
  if (width >= BREAKPOINTS['2xl']) return '2xl'
  if (width >= BREAKPOINTS.xl) return 'xl'
  if (width >= BREAKPOINTS.lg) return 'lg'
  if (width >= BREAKPOINTS.md) return 'md'
  if (width >= BREAKPOINTS.sm) return 'sm'
  return 'xs'
}

// ---- 共享单例：整个应用只挂一组 matchMedia 监听器 ----
const isBrowser = typeof window !== 'undefined' && typeof window.matchMedia === 'function'

const width = ref<number>(isBrowser ? window.innerWidth : 1280)
const name = ref<BpName>(nameForWidth(width.value))

if (isBrowser) {
  const update = (): void => {
    width.value = window.innerWidth
    name.value = nameForWidth(width.value)
  }
  // 监听每个断点边界（min-width），任一跨越即重算
  for (const min of Object.values(BREAKPOINTS)) {
    const mql = window.matchMedia(`(min-width: ${min}px)`)
    mql.addEventListener('change', update)
  }
  // 这些监听器随应用生命周期常驻，无需清理（单例）
}

export interface UseBreakpointReturn {
  /** 当前视口宽度（px），响应式 */
  width: Ref<number>
  /** 当前断点名，响应式 */
  name: Ref<BpName>
  isXs: ComputedRef<boolean>
  isSm: ComputedRef<boolean>
  isMd: ComputedRef<boolean>
  isLg: ComputedRef<boolean>
  isXl: ComputedRef<boolean>
  is2xl: ComputedRef<boolean>
  /** < md，手机（含大屏手机 sm 段） */
  isMobile: ComputedRef<boolean>
  /** md 段：平板竖屏 768–1023 */
  isTablet: ComputedRef<boolean>
  /** ≥ lg：笔记本 / 桌面 */
  isDesktop: ComputedRef<boolean>
  /** ≥ 指定断点（含） */
  from: (bp: BpName) => ComputedRef<boolean>
  /** < 指定断点 */
  until: (bp: BpName) => ComputedRef<boolean>
}

export function useBreakpoint(): UseBreakpointReturn {
  const minOf = (bp: BpName): number => (bp === 'xs' ? 0 : BREAKPOINTS[bp])

  const isXs = computed(() => name.value === 'xs')
  const isSm = computed(() => name.value === 'sm')
  const isMd = computed(() => name.value === 'md')
  const isLg = computed(() => name.value === 'lg')
  const isXl = computed(() => name.value === 'xl')
  const is2xl = computed(() => name.value === '2xl')

  const isMobile = computed(() => width.value < BREAKPOINTS.md)
  const isTablet = computed(() => width.value >= BREAKPOINTS.md && width.value < BREAKPOINTS.lg)
  const isDesktop = computed(() => width.value >= BREAKPOINTS.lg)

  const from = (bp: BpName): ComputedRef<boolean> => computed(() => width.value >= minOf(bp))
  const until = (bp: BpName): ComputedRef<boolean> => computed(() => width.value < minOf(bp))

  // useBreakpoint 只返回 computed，本身不新增副作用；此处保留 onScopeDispose
  // 以便未来若切换为 per-instance 监听器时可安全清理。
  onScopeDispose(() => {})

  return {
    width,
    name,
    isXs,
    isSm,
    isMd,
    isLg,
    isXl,
    is2xl,
    isMobile,
    isTablet,
    isDesktop,
    from,
    until,
  }
}
