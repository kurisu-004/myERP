// 送货单 Excel 导出 API（PR-F 2026-07-17 重设计）

import { api } from '@/api/http'

export interface GenerateDeliveryNotePayload {
  /** 雪花 ID 字符串列表 */
  part_ids: string[]
}

/**
 * POST /api/v1/delivery-notes/generate
 * 返回 Blob，content-type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet。
 *
 * 后端按所选零件所属 L1 root 的 `serial_prefix` 选对应 xlsx 模板：
 * - F → docs/example/送货单_法拉.xlsx
 * - L → docs/example/送货单_路达.xlsx
 * - 未配置 / 跨客户 / 状态非 READY_TO_SHIP → BizError JSON
 *
 * Content-Disposition: attachment; filename="delivery_note_<prefix>_<yyyymmdd>.xlsx"
 */
export async function generateDeliveryNote(
  partIds: string[],
): Promise<Blob> {
  const resp = await api.post<Blob>(
    '/delivery-notes/generate',
    { part_ids: partIds },
    { responseType: 'blob' },
  )
  return resp.data
}