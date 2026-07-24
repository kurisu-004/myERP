// historicalPriceExcelParser.ts
//
// 解析「历史价确认单」Excel（docs/example/历史价确认单 (1).xlsx）主表
// `历史价确认单明细` 为 BidRow[]。纯函数：仅依赖 xlsx，不触发网络 / DOM。
//
// 2026-07-24 新增：复用 bidExcelParser 的 BidRow / ParseResult 契约，
// 输出与「应标」解析结果对齐 → applyExcelToAll 不需要改。
//
// 与应标的差异：
// - 无「紧急状态」列 → isUrgent 全部 false（这是格式本身的特性，不是 bug）
// - 无「申请人所在一级部门」代码列 → deptCode 留空字符串，deptName 正常
// - 无「方案/设计图纸」/「加工类型」/「备注」列 → 对应 BidRow 字段全部 null
// - 字段名差异：
//   申请部门   ↔ 申请人所在一级部门名称
//   交期(天)   ↔ 预估交期天数
//   物料编号   ↔ 物料编号
//   物料名称   ↔ 货物(劳务)名称
//   采购数量   ↔ 计划数量
//   含税单价   ↔ 含税单价
//   含税价格   ↔ 含税价格
// - 有「税率」列（13% 之类的百分比），但 BidRow / Part / remarkText 都没有
//   税率字段，故解析时**直接丢弃**（不进 REQUIRED_HEADERS，不进 BidRow）。

import * as XLSX from 'xlsx'
import {
  addDays,
  cleanText,
  parseDecimalOrNull,
  parseIntSafe,
} from './xlsxParseUtils'
import type {
  BidRow,
  ParseError,
  ParseResult,
} from './bidExcelParser'

const SHEET_NAME = '历史价确认单明细'

const REQUIRED_HEADERS = [
  '申请部门',
  '申请人',
  '交期(天)',
  '物料编号',
  '物料名称',
  '采购数量',
  '含税单价',
  '含税价格',
]

/**
 * 解析历史价确认单 workbook 为 BidRow[]。
 *
 * @param workbook 由 `XLSX.read(arrayBuffer)` 解析得到
 * @param today   YYYY-MM-DD 形式的「今天」（建议 Asia/Shanghai 当天）
 *
 * 错误条件（与 parseBidExcel 一致）：
 * - sheet 名不是 `历史价确认单明细` → 抛 Error
 * - 必需列缺失 → 抛 Error
 *
 * 行级软行为：
 * - 缺申请人/物料编号/物料名称 → rowErrors 推入（行仍保留，方便前端展示）
 * - 采购数量/交期(天) 无效 → rowErrors 推入
 * - 含税单价为负 → rowWarnings 推入，按 0 处理
 * - 物料编号重复 → rowWarnings 推入
 * - 整行空 → 静默跳过
 */
export function parseHistoricalPriceExcel(
  workbook: XLSX.WorkBook,
  today: string,
): ParseResult {
  const sheet = workbook.Sheets[SHEET_NAME]
  if (!sheet) {
    const available = workbook.SheetNames.join(', ')
    throw new Error(
      `请上传正确的历史价确认单 Excel（应包含 "${SHEET_NAME}" sheet），当前文件 sheet: ${available}`,
    )
  }

  // 用 header:1 模式读第一行做列名校验；缺整列才能识别。
  const rawHeader: unknown[][] = XLSX.utils.sheet_to_json(sheet, {
    header: 1,
    raw: false,
    blankrows: false,
  })
  const headerRow: unknown[] = (rawHeader[0] ?? []) as unknown[]
  if (headerRow.length === 0) {
    return { rows: [], errors: [], warnings: ['Excel 没有数据行'] }
  }
  const headers = headerRow.map((h) => cleanText(h))
  const missing = REQUIRED_HEADERS.filter((h) => !headers.includes(h))
  if (missing.length > 0) {
    throw new Error(`Excel 缺少必需列：${missing.join('、')}`)
  }

  const rawRows: Record<string, unknown>[] = XLSX.utils.sheet_to_json(sheet, {
    defval: null,
    raw: false,
    blankrows: false,
  })

  if (rawRows.length === 0) {
    return { rows: [], errors: [], warnings: ['Excel 没有数据行'] }
  }

  const rows: BidRow[] = []
  const errors: ParseError[] = []
  const seenDrawingNo = new Set<string>()

  for (let i = 0; i < rawRows.length; i++) {
    const raw = rawRows[i]
    const rowNumber = i + 2 // 0-index + 跳过表头

    const deptName = cleanText(raw['申请部门'])
    const applicantName = cleanText(raw['申请人'])
    const drawingNo = cleanText(raw['物料编号'])
    const partName = cleanText(raw['物料名称'])
    const quantityRaw = raw['采购数量']
    const unitPriceRaw = parseDecimalOrNull(raw['含税单价'])
    const unitPrice = unitPriceRaw == null || unitPriceRaw < 0 ? 0 : unitPriceRaw
    const deliveryDaysRaw = raw['交期(天)']
    const totalPriceRaw = parseDecimalOrNull(raw['含税价格'])

    // 整行空：静默跳过（防止末尾回车产生的空行污染）
    const allEmpty = [deptName, applicantName, drawingNo, partName].every(
      (s) => !s,
    )
    if (allEmpty) continue

    const rowWarnings: string[] = []
    const rowErrors: string[] = []

    if (!applicantName) rowErrors.push('申请人不能为空')
    if (!drawingNo) rowErrors.push('物料编号不能为空')
    if (!partName) rowErrors.push('物料名称不能为空')

    let quantity = 0
    if (quantityRaw == null || quantityRaw === '') {
      rowErrors.push('采购数量不能为空')
    } else {
      // Excel 单元格常带千分位逗号（如 "1,500"），先剥离再 parseIntSafe 走相同路径
      const n = parseIntSafe(quantityRaw, null)
      if (n == null || n <= 0) {
        rowErrors.push(`采购数量必须为正整数（当前：${quantityRaw}）`)
      } else {
        quantity = n
      }
    }

    let deliveryDays = 0
    const ddParsed = parseIntSafe(deliveryDaysRaw)
    if (ddParsed == null || ddParsed <= 0) {
      rowErrors.push(`交期(天)必须为正整数（当前：${deliveryDaysRaw}）`)
    } else {
      deliveryDays = ddParsed
    }

    if (unitPriceRaw != null && unitPriceRaw < 0) {
      rowWarnings.push('含税单价为负数，已按 0 处理')
    }

    if (drawingNo && seenDrawingNo.has(drawingNo)) {
      rowWarnings.push('本批次有重复图号')
    } else if (drawingNo) {
      seenDrawingNo.add(drawingNo)
    }

    // 历史价确认单自带「含税价格」列，优先用；缺 / 非法 → 用 unitPrice * quantity 兜底
    let totalPrice = unitPrice * quantity
    if (totalPriceRaw != null && totalPriceRaw >= 0) {
      totalPrice = totalPriceRaw
    }

    const plannedDeliveryDate = deliveryDays > 0 ? addDays(today, deliveryDays) : ''

    if (rowErrors.length > 0) {
      errors.push({ rowNumber, message: rowErrors.join('；') })
    }

    rows.push({
      rowNumber,
      applicantName,
      drawingNo,
      partName,
      quantity,
      unitPrice,
      totalPrice,
      isUrgent: false, // 历史价格式无紧急状态列
      deliveryDays,
      plannedDeliveryDate,
      deptCode: '', // 历史价格式无一级部门代码列
      deptName,
      designDrawingLabel: null,
      processTypeLabel: null,
      remarkText: null,
      warnings: rowWarnings,
    })
  }

  return { rows, errors, warnings: [] }
}