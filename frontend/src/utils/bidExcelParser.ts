// bidExcelParser.ts
//
// 解析「法拉电子应标」Excel（docs/example/供应商招标项目应标 (9).xlsx）主表
// `招标项目-标的` 为 BidRow[]。纯函数：仅依赖 xlsx，不触发网络 / DOM。
//
// 字段映射详见 plan docs/.../excel-snug-zephyr.md 第三节。

import * as XLSX from 'xlsx'
import {
  addDays,
  cleanText,
  parseDecimalOrNull,
  parseIntSafe,
} from './xlsxParseUtils'

/** 解析后的一行（不含 customer_id / applicant_id，page 层去解析）。 */
export interface BidRow {
  /** 1-based，对应 Excel 真实行号（1 = 表头，2 = 第 1 条数据）。 */
  rowNumber: number
  applicantName: string
  drawingNo: string
  partName: string
  quantity: number
  unitPrice: number
  totalPrice: number
  isUrgent: boolean
  deliveryDays: number
  /** YYYY-MM-DD；deliveryDays 无效时为空串。 */
  plannedDeliveryDate: string
  /** 显示用：申请人所在一级部门代码（F01/F05/...） */
  deptCode: string
  /** 显示用：申请人所在一级部门名称（一厂/五厂/...），同时是 page 层
   *  解析 L2 客户 id 的锚。 */
  deptName: string
  /** 方案/设计图纸 JSON 数组里第一个 fileName（仅显示，不自动上传）。 */
  designDrawingLabel: string | null
  processTypeLabel: string | null
  remarkText: string | null
  /** 行级软警告（紧急状态未识别 / 单价异常 / 重复图号 ...） */
  warnings: string[]
}

export interface ParseError {
  rowNumber: number
  message: string
}

export interface ParseResult {
  rows: BidRow[]
  errors: ParseError[]
  /** 文件级软警告（如「检测到多个分厂」由 page 层补，这里只放解析期发现）。 */
  warnings: string[]
}

const SHEET_NAME = '招标项目-标的'

const REQUIRED_HEADERS = [
  '申请人',
  '申请人所在一级部门',
  '申请人所在一级部门名称',
  '物料编号',
  '货物(劳务)名称',
  '紧急状态',
  '计划数量',
  '含税单价',
  '预估交期天数',
]

/** `方案/设计图纸` 单元格内嵌的 JSON 数组里抽第一个 fileName。失败 → null。 */
function extractFirstFileName(raw: string): string | null {
  if (!raw) return null
  const trimmed = raw.trim()
  const arrStart = trimmed.indexOf('[')
  if (arrStart < 0) return null
  const arrEnd = trimmed.lastIndexOf(']')
  const jsonStr = arrEnd > arrStart ? trimmed.slice(arrStart, arrEnd + 1) : trimmed.slice(arrStart)
  try {
    const arr = JSON.parse(jsonStr)
    if (Array.isArray(arr) && arr.length > 0) {
      const first = arr[0]
      if (first && typeof first.fileName === 'string') {
        return first.fileName
      }
    }
  } catch {
    // 不是合法 JSON（可能被 Excel 截断），静默返回 null
  }
  return null
}

/**
 * 解析 Excel workbook 为 BidRow[]。
 *
 * @param workbook 由 `XLSX.read(arrayBuffer)` 解析得到
 * @param today   YYYY-MM-DD 形式的「今天」（建议 Asia/Shanghai 当天）
 */
export function parseBidExcel(
  workbook: XLSX.WorkBook,
  today: string,
): ParseResult {
  const sheet = workbook.Sheets[SHEET_NAME]
  if (!sheet) {
    const available = workbook.SheetNames.join(', ')
    throw new Error(
      `请上传正确的应标 Excel（应包含 "${SHEET_NAME}" sheet），当前文件 sheet: ${available}`,
    )
  }

  // 读出 sheet 第一行（表头），用它做列名校验 —— sheet_to_json 的
  // defval:null 模式会保留空 cell 的 key，反而掩盖「整列缺失」的情况。
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
  let strayRowCount = 0

  for (let i = 0; i < rawRows.length; i++) {
    const raw = rawRows[i]
    const rowNumber = i + 2 // 0-index + 跳过表头

    const applicantName = cleanText(raw['申请人'])
    const deptCode = cleanText(raw['申请人所在一级部门'])
    const deptName = cleanText(raw['申请人所在一级部门名称'])
    const designDrawingRaw = cleanText(raw['方案/设计图纸'])
    const drawingNo = cleanText(raw['物料编号'])
    const partName = cleanText(raw['货物(劳务)名称'])
    const quantityRaw = raw['计划数量']
    const unitPriceRaw = parseDecimalOrNull(raw['含税单价'])
    const unitPrice = unitPriceRaw == null || unitPriceRaw < 0 ? 0 : unitPriceRaw
    const deliveryDaysRaw = raw['预估交期天数']
    const processType = cleanText(raw['加工类型'])
    const remark = cleanText(raw['备注'])

    // 整行空：静默跳过
    const allEmpty = [applicantName, deptCode, deptName, drawingNo, partName].every(
      (s) => !s,
    )
    if (allEmpty) continue

    // 残留行：仅「物料编号」一格有值、其他关键字段全空。典型成因是把
    // 「E42xxx  名称」整体粘到物料编号单元格。静默跳过并计入文件级
    // warning，避免向用户弹「Excel 解析告警：N 条（已忽略）」的噪音。
    const strayLike =
      !!drawingNo &&
      !applicantName &&
      !deptCode &&
      !deptName &&
      !partName &&
      (quantityRaw == null || quantityRaw === '') &&
      (deliveryDaysRaw == null || deliveryDaysRaw === '')
    if (strayLike) {
      strayRowCount++
      continue
    }

    const rowWarnings: string[] = []
    const rowErrors: string[] = []

    if (!applicantName) rowErrors.push('申请人不能为空')
    if (!drawingNo) rowErrors.push('物料编号不能为空')
    if (!partName) rowErrors.push('货物(劳务)名称不能为空')

    let quantity = 0
    if (quantityRaw == null || quantityRaw === '') {
      rowErrors.push('计划数量不能为空')
    } else {
      const n = Number(String(quantityRaw).trim())
      if (!Number.isFinite(n) || !Number.isInteger(n) || n <= 0) {
        rowErrors.push(`计划数量必须为正整数（当前：${quantityRaw}）`)
      } else {
        quantity = n
      }
    }

    let deliveryDays = 0
    const ddParsed = parseIntSafe(deliveryDaysRaw)
    if (ddParsed == null || ddParsed <= 0) {
      rowErrors.push(`预估交期天数必须为正整数（当前：${deliveryDaysRaw}）`)
    } else {
      deliveryDays = ddParsed
    }

    // 2026-07-30：批量 PDF 导入不再自动识别是否加急，默认全部不加急。
    // 用户可在前端预览表的 el-switch 单独打开加急。
    const isUrgent = false

    if (unitPriceRaw != null && unitPriceRaw < 0) {
      rowWarnings.push('含税单价为负数，已按 0 处理')
    }

    if (drawingNo && seenDrawingNo.has(drawingNo)) {
      rowWarnings.push('本批次有重复图号')
    } else if (drawingNo) {
      seenDrawingNo.add(drawingNo)
    }

    const totalPrice = unitPrice > 0 ? unitPrice * quantity : 0
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
      isUrgent,
      deliveryDays,
      plannedDeliveryDate,
      deptCode,
      deptName,
      designDrawingLabel: extractFirstFileName(designDrawingRaw),
      processTypeLabel: processType || null,
      remarkText: remark || null,
      warnings: rowWarnings,
    })
  }

  const warnings: string[] = []
  if (strayRowCount > 0) {
    warnings.push(`已忽略 ${strayRowCount} 行（仅含图号，疑似残留数据）`)
  }
  return { rows, errors, warnings }
}
