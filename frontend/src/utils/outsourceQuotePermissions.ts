/** 外协报价页面权限工具（2026-07-16，纯函数供 vitest 单测）。

不依赖 auth session；调用方把 user.roles 传进来即可。
CLERK / MANAGER 通过 manage_quotes 菜单即视为可读写；
MANAGER 还有审批权（approve / reject）；
CLERK 只能编辑 DRAFT 自己的；超界一律禁用。
*/

import type { OutsourceQuoteStatus } from '@/types/outsource'

interface RoleLike {
  MANAGER?: boolean
  CLERK?: boolean
}

/** 当前角色是否拥有任意外协报价菜单权限 */
export function hasManageQuotesRole(role: RoleLike): boolean {
  return !!(role.MANAGER || role.CLERK)
}

/** 是否能新建报价 */
export function canCreate(role: RoleLike): boolean {
  return hasManageQuotesRole(role)
}

/** 是否能编辑 / 提交审核（DRAFT 状态 + 有管理权限） */
export function canEdit(quote: { status: OutsourceQuoteStatus }, role: RoleLike): boolean {
  if (!hasManageQuotesRole(role)) return false
  return quote.status === 'DRAFT'
}

/** 是否能审批通过（仅 MANAGER + SUBMITTED） */
export function canApprove(quote: { status: OutsourceQuoteStatus }, role: RoleLike): boolean {
  if (!role.MANAGER) return false
  return quote.status === 'SUBMITTED'
}

/** 是否能拒绝（仅 MANAGER + SUBMITTED） */
export function canReject(quote: { status: OutsourceQuoteStatus }, role: RoleLike): boolean {
  if (!role.MANAGER) return false
  return quote.status === 'SUBMITTED'
}

/** 是否能撤回（CLERK 把 SUBMITTED 退回 DRAFT） */
export function canWithdraw(quote: { status: OutsourceQuoteStatus }, role: RoleLike): boolean {
  if (!hasManageQuotesRole(role)) return false
  return quote.status === 'SUBMITTED'
}

/** 是否能软删（DRAFT / REJECTED 状态） */
export function canSoftDelete(
  quote: { status: OutsourceQuoteStatus },
  role: RoleLike,
): boolean {
  if (!hasManageQuotesRole(role)) return false
  return quote.status === 'DRAFT' || quote.status === 'REJECTED'
}

/** 工具：把 useAuthSession 风格的 roles 数组转成 RoleLike 字典 */
export function rolesArrayToMap(roles: readonly string[]): RoleLike {
  return {
    MANAGER: roles.includes('MANAGER'),
    CLERK: roles.includes('CLERK'),
  }
}
