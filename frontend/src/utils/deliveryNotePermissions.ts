// 送货单权限 helper（PR-G 2026-07-22 新增）。
//
// 形态对齐 frontend/src/utils/outsourceQuotePermissions.ts。
// 这里只放「状态/角色矩阵」的纯函数；视图层把 user.roles 传进来即可。
//
// 角色矩阵（与 api/v1/delivery_note.py 对齐）：
//   - CLERK + MANAGER + INSPECTOR：list / detail / events / add / remove / submit /
//     recall / soft-delete / print
//   - 任意已登录（含 SHELF_ACCOUNT）：pickup-pending list / detail / events / scan / pickup
//     （service 层再做 driver.work_type.code == '送货司机' 校验）

import type { DeliveryNoteStatus } from '@/types/deliveryNote'

export interface RoleMapLike {
  MANAGER?: boolean
  CLERK?: boolean
  INSPECTOR?: boolean
}

export function hasManageNoteRole(role: RoleMapLike): boolean {
  return Boolean(role.MANAGER || role.CLERK || role.INSPECTOR)
}

export function canAddRemoveParts(
  status: DeliveryNoteStatus,
  role: RoleMapLike,
): boolean {
  if (!hasManageNoteRole(role)) return false
  return status === 'DRAFT' || status === 'SUBMITTED'
}

export function canSubmit(
  status: DeliveryNoteStatus,
  role: RoleMapLike,
): boolean {
  return hasManageNoteRole(role) && status === 'DRAFT'
}

export function canRecall(
  status: DeliveryNoteStatus,
  role: RoleMapLike,
): boolean {
  return hasManageNoteRole(role) && status === 'SUBMITTED'
}

export function canSoftDelete(
  status: DeliveryNoteStatus,
  role: RoleMapLike,
): boolean {
  return hasManageNoteRole(role) && status === 'DRAFT'
}

// 打印按钮：管理角色 + 至少 1 个零件；不限 status（草稿可预览打印）。
export function canPrint(role: RoleMapLike, partCount: number): boolean {
  return hasManageNoteRole(role) && partCount > 0
}

// 司机领取：状态 = SUBMITTED 且 count == 0（driver 无角色要求，前端只显示按钮）
// 实际 driver 校验在 service 层。
export function canPickup(status: DeliveryNoteStatus): boolean {
  return status === 'SUBMITTED'
}

// 详情页允许进入：所有合法状态皆可；非有效 status 直接不显示页面。
export function canView(status: DeliveryNoteStatus): boolean {
  return (
    status === 'DRAFT' ||
    status === 'SUBMITTED' ||
    status === 'PICKED_UP' ||
    status === 'ARCHIVED'
  )
}

// 一览 default statuses by role（与 outsource_quote 对齐）
export function defaultStatusesForRole(
  role: RoleMapLike,
): DeliveryNoteStatus[] {
  if (role.MANAGER) return ['DRAFT', 'SUBMITTED', 'PICKED_UP', 'ARCHIVED']
  if (role.CLERK) return ['DRAFT', 'SUBMITTED']
  if (role.INSPECTOR) return ['DRAFT', 'SUBMITTED', 'PICKED_UP', 'ARCHIVED']
  return ['SUBMITTED', 'PICKED_UP']  // 司机扫码台默认只关心待送货/已领
}
