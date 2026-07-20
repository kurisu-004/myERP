// composables/usePermissions.ts
//
// 统一当前账号的角色 computed（PR-I 2026-07-20）。
// 用 `useAuthSession().hasRole()` 包一层，避免每个组件都重复 `hasRole('X')` 的样板。
//
// 用法：
//   const { isInspector, isClerk, isManager } = usePermissions()
//   <el-button v-if="!isInspector" ...>...</el-button>
//
// 注意：
// - 这里返回的是 `ComputedRef<boolean>`，不是 boolean；模板里直接用即可。
// - 不要在 service 层或 composable 内部「读一次 hasRole 当常量」——角色可能在登录后变更。

import { computed, type ComputedRef } from 'vue'
import { useAuthSession } from './useAuthSession'

export interface PermissionsApi {
  isManager: ComputedRef<boolean>
  isClerk: ComputedRef<boolean>
  isInspector: ComputedRef<boolean>
  isCncProgrammer: ComputedRef<boolean>
  isShelfAccount: ComputedRef<boolean>
}

export function usePermissions(): PermissionsApi {
  const { hasRole } = useAuthSession()
  return {
    isManager: computed(() => hasRole('MANAGER')),
    isClerk: computed(() => hasRole('CLERK')),
    isInspector: computed(() => hasRole('INSPECTOR')),
    isCncProgrammer: computed(() => hasRole('CNC_PROGRAMMER')),
    isShelfAccount: computed(() => hasRole('SHELF_ACCOUNT')),
  }
}