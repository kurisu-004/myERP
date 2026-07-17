/**
 * 扫码台事件总线（模块级单例）。
 *
 * 用途：在 PICK_UP / RETURN / INSPECT 等状态变更后，让 HeldPartsBadge 等
 *       跨组件订阅者无需走 Vue Router 传参即可同步刷新。
 *
 * 设计：
 * - heldVersion: 自增 ref，作为「持有件变化」的信号量。任何订阅者 watch 它即可。
 * - onHeldChanged(): 显式注册回调 + 返回 off 函数（与 Vue 生态一致）。
 *
 * 不要在此总线放大型 payload（保持轻量信号语义）。
 */

import { ref, type Ref } from 'vue'

/** 模块级信号：当前工人持有件变化（领取/放回/送检后） */
const heldVersion = ref(0)

export type HeldChangeHandler = (newVersion: number) => void
const listeners = new Set<HeldChangeHandler>()

export function useScanBus(): {
  emitHeldChanged: () => void
  onHeldChanged: (fn: HeldChangeHandler) => () => void
  heldVersion: Readonly<Ref<number>>
} {
  return {
    /** 自增信号 + 通知监听者 */
    emitHeldChanged(): void {
      heldVersion.value++
      listeners.forEach((fn) => fn(heldVersion.value))
    },
    /** 监听持有件变化（返回 off 函数，组件 unmount 时调用） */
    onHeldChanged(fn: HeldChangeHandler): () => void {
      listeners.add(fn)
      return () => {
        listeners.delete(fn)
      }
    },
    /** 当前版本号 ref，徽章组件 watch 它 */
    heldVersion: heldVersion as Readonly<Ref<number>>,
  }
}