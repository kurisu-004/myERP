import { computed, type ComputedRef } from 'vue'
import { useBreakpoint } from './useBreakpoint'

export interface DialogSizeOpts {
  /** 桌面首选宽度（px 数字或带单位字符串），默认 520 */
  desktopWidth?: number | string
  /** 手机(<sm)是否走全屏 el-dialog。默认 false（改用 92vw 宽 + 6vh top） */
  fullscreenOnMobile?: boolean
}

export interface DialogSizeReturn {
  /** 绑定到 el-dialog :width */
  width: ComputedRef<string>
  /** 绑定到 el-dialog :top */
  top: ComputedRef<string>
  /** 绑定到 el-dialog :fullscreen */
  fullscreen: ComputedRef<boolean>
}

/**
 * 统一的对话框尺寸策略：
 * - 桌面：固定宽度（desktopWidth），top 15vh（EP 默认）
 * - 手机(<sm)：fullscreenOnMobile=true 走全屏；否则 92vw 宽 + 6vh top，近全屏但保留边距
 */
export function useDialogSize(opts: DialogSizeOpts = {}): DialogSizeReturn {
  const { until } = useBreakpoint()
  const isPhone = until('sm')
  const desktop =
    typeof opts.desktopWidth === 'number'
      ? `${opts.desktopWidth}px`
      : (opts.desktopWidth ?? '520px')

  const fullscreen = computed(() => Boolean(opts.fullscreenOnMobile) && isPhone.value)
  const width = computed(() => (isPhone.value ? '92vw' : desktop))
  const top = computed(() => (isPhone.value ? '6vh' : '15vh'))

  return { width, top, fullscreen }
}
