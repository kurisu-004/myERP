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
// 2026-07-22 扩展：除了下划线，还兼容空白分隔（单/多空格）和「图号正则锚定」+ 后续
// 分隔符（空白 / `_` / `-`），让用户手动改名时混用「E42xxx  名称.pdf」「E42xxx 名称.pdf」
// 「E42xxx-名称.pdf」等格式都能被识别。MD5-hash 服务端命名 / 完全无法识别 → 双 null
// （调用方保留「需手动填写」的兜底行为）。
//
// 优先级：
//   1) 首个 '_'                  —— 保留原行为
//   2) 首个空白 run (\s+)        —— 双空格 / 单空格
//   3) 图号正则 ^E\d{2}[A-Z0-9]{0,2}\w* + 后续首个 [\s_\-]+ —— 兜底未来其他分隔符
//   4) 都不命中 → { null, null }
//
// 服务端不做此解析（DB 存原始 UTF-8 original_filename，仅在 UI 预填用）。

export interface ParsedDrawingFilename {
  drawingNo: string | null
  partName: string | null
}

/** 匹配「图号」开头：以 E + 2 位数字 + 0-2 位 [A-Z0-9] + 任意 \w。 */
const DRAWING_NO_LEAD_RE = /^E\d{2}[A-Z0-9]{0,2}\w*/

export function parseDrawingFilename(filename: string): ParsedDrawingFilename {
  // 去掉扩展名（仅 .pdf / .PDF，区分大小写，避免误切）
  const noExt = filename.replace(/\.pdf$/i, '')
  const noExtTrim = noExt.trim()
  if (!noExtTrim) return { drawingNo: null, partName: null }

  // 1) '_' —— 保留原行为
  const u = noExtTrim.indexOf('_')
  if (u >= 0) {
    return {
      drawingNo: noExtTrim.slice(0, u).trim() || null,
      partName: noExtTrim.slice(u + 1).trim() || null,
    }
  }

  // 2) 首个空白 run —— 双空格 / 单空格都覆盖
  const wsMatch = noExtTrim.match(/\s+/)
  if (wsMatch && wsMatch.index !== undefined && wsMatch.index > 0) {
    return {
      drawingNo: noExtTrim.slice(0, wsMatch.index).trim() || null,
      partName: noExtTrim.slice(wsMatch.index + wsMatch[0].length).trim() || null,
    }
  }

  // 3) 图号正则锚定 + 后续首个 [\s_\-] 分隔点
  const head = noExtTrim.match(DRAWING_NO_LEAD_RE)
  if (head) {
    const rest = noExtTrim.slice(head[0].length)
    const boundary = rest.match(/[\s_\-]+/)
    if (boundary && boundary.index !== undefined) {
      return {
        drawingNo: head[0],
        partName:
          rest.slice(boundary.index + boundary[0].length).trim() || null,
      }
    }
  }

  // 4) MD5 / 完全无法识别
  return { drawingNo: null, partName: null }
}