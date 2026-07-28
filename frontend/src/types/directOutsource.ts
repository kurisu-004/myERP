/** 直接发送外协候选（已弃用，被 OutsourceSendableItem.send_mode='DIRECT' 取代，2026-07-28）。
 *
 * 保留此文件以兼容旧 API 响应（旧 listDirectOutsourceCandidates）；新代码请使用
 * @/types/outsource.ts 中的 OutsourceSendableItem。
 */

export interface DirectOutsourceCompanyOption {
  id: string
  name: string
}

export interface DirectOutsourceCandidateItem {
  /** 乐观锁版本号（零件 TPart.version） */
  version: number
  part_id: string
  part_serial_no: string | null
  part_drawing_no: string | null
  part_name: string | null
  quantity: number | null
  planned_delivery_date: string | null
  is_urgent: boolean
  customer_path: string | null
  next_process_id: string
  next_process_name: string | null
  requires_approval: false
  status_label: 'sendable'
  company_options: DirectOutsourceCompanyOption[]
}

export interface DirectOutsourceCandidateListResult {
  items: DirectOutsourceCandidateItem[]
  total: number
  limit: number
  offset: number
}