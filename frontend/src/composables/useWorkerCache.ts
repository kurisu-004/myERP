// composables/useWorkerCache.ts
//
// 包一层对 api/worker.ts 里 findWorkerByBadge / invalidateWorkerCache 的访问，
// 给组件一个统一的 composable 入口，不直接 import api 层。
//
// 用法：
//   const { findByBadge, invalidate } = useWorkerCache()
//   const worker = await findByBadge(code)

import { findWorkerByBadge, invalidateWorkerCache } from '@/api/worker'

export function useWorkerCache() {
  return {
    findByBadge: findWorkerByBadge,
    invalidate: invalidateWorkerCache,
  }
}
