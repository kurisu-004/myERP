// 送货单 Excel 导出 API（PR-F 2026-07-17 重设计；2026-07-20 加 template 显式选择）

import { api } from '@/api/http'

/** 显式送货单模板（F=法拉 / L=路达）；None = 后端按客户前缀自动分发。 */
export type DeliveryNoteTemplate = 'F' | 'L'

export interface GenerateDeliveryNotePayload {
  /** 雪花 ID 字符串列表 */
  part_ids: string[]
  /** 显式指定模板 prefix（覆盖后端自动分发） */
  template?: DeliveryNoteTemplate
}

/**
 * POST /api/v1/delivery-notes/generate
 * 返回 Blob，content-type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet。
 *
 * 后端按所选零件所属 L1 root 的 `serial_prefix` 自动选对应 xlsx 模板：
 * - F → template/delivery_note_fala.xlsx
 * - L → template/delivery_note_luda.xlsx
 * 调用方也可显式传 `template: 'F' | 'L'` 覆盖自动分发；显式值必须与
 * 所选零件所属 L1 root 一致，否则 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED。
 *
 * 未配置 / 跨客户 / 状态非 READY_TO_SHIP / 超模板容量 →
 *   BIZ_DELIVERY_* 400 系列 BizError JSON。
 *
 * Content-Disposition: attachment; filename="delivery_note_<prefix>_<yyyymmdd>.xlsx"
 */
export async function generateDeliveryNote(
  partIds: string[],
  template?: DeliveryNoteTemplate,
): Promise<Blob> {
  const payload: GenerateDeliveryNotePayload = { part_ids: partIds }
  if (template) payload.template = template
  const resp = await api.post<Blob>(
    '/delivery-notes/generate',
    payload,
    { responseType: 'blob' },
  )
  return resp.data
}