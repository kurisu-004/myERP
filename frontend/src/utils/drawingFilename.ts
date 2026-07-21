// 2026-07-21 新增：解析「图号_零件名称.pdf」格式的文件名。
//
// 规则（与产品确认）：
//   - 在第一个「_」处切分（不是最后一个「.」）；
//   - drawing_no = 第一段；name = 第二段去掉 .pdf/.PDF 后缀；
//   - 没有「_」→ drawing_no 留空，name 留空（让用户手动填）；
//   - 多下划线仅在第一个处切；「A_B_C」→ drawingNo='A' name='B_C'；
//   - 前后空白自动 strip；
//   - 切完后任一段为空字符串 → 视作 null（避免后端 strip 后空串 422）。
//
// 服务端不做此解析（DB 存原始 UTF-8 original_filename，仅在 UI 预填用）。

export interface ParsedDrawingFilename {
  drawingNo: string | null
  partName: string | null
}

export function parseDrawingFilename(filename: string): ParsedDrawingFilename {
  // 去掉扩展名（仅 .pdf / .PDF，区分大小写，避免误切）
  const noExt = filename.replace(/\.pdf$/i, '')
  const idx = noExt.indexOf('_')
  if (idx < 0) return { drawingNo: null, partName: null }
  const drawingNo = noExt.slice(0, idx).trim() || null
  const partName = noExt.slice(idx + 1).trim() || null
  return { drawingNo, partName }
}