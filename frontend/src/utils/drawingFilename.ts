// 2026-07-21 新增：解析「图号_零件名称.pdf」格式的文件名。
//
// v1（2026-07-21）：单规则——只切首 `_`，drawing_no = 第一段、name = 第二段去 `.pdf`/`.PDF` 后缀。
//  v1 规则（与产品确认）：
//    - 在第一个「_」处切分（不是最后一个「.」）；
//    - drawing_no = 第一段；name = 第二段去掉 .pdf/.PDF 后缀；
//    - 多下划线仅在第一个处切；「A_B_C」→ drawingNo='A' name='B_C'；
//    - 前后空白自动 strip；
//    - 切完后任一段为空字符串 → 视作 null（避免后端 strip 后空串 422）。
//
// v2（2026-07-22）：扩展为多级 fallback——
//    2) 首个空白 run (\s+) —— 双空格 / 单空格
//    3) 图号正则 ^E\d{2}[A-Z0-9]{0,2}\w* + 后续首个 [\s_\-]+ 边界
//
// v3（2026-07-31）：**收紧回 v1**。理由：图纸名内本身就常含空白 / 描述性字符
// （「拉力计连接棒（引线强度检测）」类描述），让空白当分隔符会把
// 「E42X_名称 修订版 v2.pdf」这种自然命名误切成 `{drawingNo:'E42X', partName:'名称'}`
// ——后续字符丢光。hyphen / 边界正则同理容易误伤。决定只用首 `_` 作唯一分隔符；
// 没有 `_` → 双 null 让用户在 `drawing_no` / `name` 输入框手填
// （UI 不阻塞提交：`PartBatchNew.vue` 仅把空字段标红，路由放过）。
//
// 服务端不做此解析（DB 存原始 UTF-8 original_filename，仅在 UI 预填用）。

export interface ParsedDrawingFilename {
  drawingNo: string | null
  partName: string | null
}

export function parseDrawingFilename(filename: string): ParsedDrawingFilename {
  // 去掉扩展名（仅 .pdf / .PDF，不区分大小写，避免误切）；strip 前后空白
  const noExtTrim = filename.replace(/\.pdf$/i, '').trim()
  if (!noExtTrim) return { drawingNo: null, partName: null }

  // 唯一规则：首个 '_' 切；无 '_' 一律双 null 让用户手填
  const u = noExtTrim.indexOf('_')
  if (u < 0) return { drawingNo: null, partName: null }

  return {
    drawingNo: noExtTrim.slice(0, u).trim() || null,
    partName: noExtTrim.slice(u + 1).trim() || null,
  }
}
