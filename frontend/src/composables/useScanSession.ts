// composables/useScanSession.ts
//
// 工位扫码台跨路由共享 session：
//   - worker：扫工牌成功后存到这里，下一步页面共享。
//   - action：选了 PICK_UP / RETURN / INSPECT 后存到这里，下一步页面共享。
// 任意步骤都可 reset() 清空（重新扫工牌 / 退至首页）。
//
// 设计要点：
// - 模块级单例，跨组件共享（与 useBarcodeScanner 一致；useWorkerCache 已删，
//   扫码定位工牌改为 api/worker.findWorkerByBadge 直打后端）。
// - 不引入 Pinia：session 只在这三个扫码路由之间流动，没其他消费者。
// - 用一个 requireXxx() 守卫把"未扫工牌就直接进操作选择/扫码页"挡掉。

import { ref, type Ref } from 'vue'
import type { Router } from 'vue-router'
import type { Worker } from '@/types/worker'

export type WorkAction = 'PICK_UP' | 'RETURN' | 'INSPECT' | 'DELIVER'

export const WORK_ACTION_VALUES: readonly WorkAction[] = ['PICK_UP', 'RETURN', 'INSPECT', 'DELIVER'] as const

/** 路由 query 里用的简写：?action=pickup|return|inspect|deliver */
export type WorkActionSlug = 'pickup' | 'return' | 'inspect' | 'deliver'

export const ACTION_LABEL: Record<WorkAction, string> = {
  PICK_UP: '取件',
  RETURN: '放回',
  INSPECT: '送检',
  DELIVER: '送货',
}

export const ACTION_TAG_TYPE: Record<WorkAction, 'primary' | 'warning' | 'success' | 'danger'> = {
  PICK_UP: 'primary',
  RETURN: 'warning',
  INSPECT: 'success',
  DELIVER: 'danger',
}

const SLUG_TO_ACTION: Record<WorkActionSlug, WorkAction> = {
  pickup: 'PICK_UP',
  return: 'RETURN',
  inspect: 'INSPECT',
  deliver: 'DELIVER',
}

const ACTION_TO_SLUG: Record<WorkAction, WorkActionSlug> = {
  PICK_UP: 'pickup',
  RETURN: 'return',
  INSPECT: 'inspect',
  DELIVER: 'deliver',
}

// ============ 单例状态 ============
const worker = ref<Worker | null>(null)
const action = ref<WorkAction | null>(null)

export function useScanSession() {
  function setWorker(w: Worker | null): void {
    worker.value = w
  }

  function setAction(a: WorkAction | null): void {
    action.value = a
  }

  function reset(): void {
    worker.value = null
    action.value = null
  }

  /** 守卫：worker 缺失则跳回扫码入口；返回是否通过。 */
  function requireWorker(router: Router): boolean {
    if (worker.value) return true
    void router.replace('/scan/badge')
    return false
  }

  /** 守卫：worker + action 都齐；缺一个就跳回对应入口；返回是否通过。 */
  function requireWorkerAndAction(router: Router): boolean {
    if (!worker.value) {
      void router.replace('/scan/badge')
      return false
    }
    if (!action.value) {
      void router.replace('/scan/action')
      return false
    }
    return true
  }

  /** slug ↔ action 互转，缺省返回 null 让调用方自己 redirect。 */
  function slugToAction(slug: unknown): WorkAction | null {
    if (typeof slug !== 'string') return null
    return SLUG_TO_ACTION[slug as WorkActionSlug] ?? null
  }

  function actionToSlug(a: WorkAction): WorkActionSlug {
    return ACTION_TO_SLUG[a]
  }

  return {
    worker: worker as Ref<Worker | null>,
    action: action as Ref<WorkAction | null>,
    setWorker,
    setAction,
    reset,
    requireWorker,
    requireWorkerAndAction,
    slugToAction,
    actionToSlug,
  }
}