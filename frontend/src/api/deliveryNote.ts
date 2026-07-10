// 送货单 Excel 导出 API（PR-B 2026-07-10）

import { api } from '@/api/http'

export interface GenerateDeliveryNotePayload {
  /** 雪花 ID 字符串列表 */
  part_ids: string[]
}

/**
 * POST /api/v1/delivery-notes/generate
 * 返回 Blob，content-type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet。
 *
 * 模板文件路径由后端 DELIVERY_NOTE_TEMPLATE_PATH 控制；
 * 模板缺失 / 缺 sheet / part_ids 为空等错误都会以普通 BizError JSON 返回。
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