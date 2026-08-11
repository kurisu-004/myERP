// purchaseOrderExcelParser.ts
//
// 解析采购订单 Excel 的「基本资料」与「采购订单明细」sheet。
// 纯函数：仅依赖 xlsx/dayjs，不触发网络或 DOM。

import dayjs from 'dayjs'
import * as XLSX from 'xlsx'

import { cleanText } from './xlsxParseUtils'

export interface PurchaseOrderExcelItem {
  /** 1-based Excel 真实行号。 */
  rowNo: number
  /** 采购订单行号，例如 10、20。 */
  lineNo: string
  deleted: boolean
  drawingNo: string
  name: string
  /** YYYY-MM-DD 或 null。 */
  deliveryDate: string | null
  unitPrice: number | null
  shippableQty: number | null
}

export interface ParsedPurchaseOrder {
  docNo: string
  items: PurchaseOrderExcelItem[]
  /** 解析失败的致命错误。 */
  errors: string[]
  /** 不阻塞导入的格式或跳行提醒。 */
  warnings: string[]
}

const BASE_SHEET_NAME = '基本资料'
const DETAIL_SHEET_NAME = '采购订单明细'
const HEADER_ROW_INDEX = 3
const DATA_START_ROW_INDEX = 4

const REQUIRED_DETAIL_HEADERS = ['物料代码', '订单物料描述'] as const
const OPTIONAL_DETAIL_HEADERS = [
  '采购订单行号',
  '已删除',
  '交货日期',
  '含税价',
  '可出货数量',
] as const

const EXPECTED_DETAIL_COLUMNS: Record<string, number> = {
  采购订单行号: 0,
  已删除: 2,
  物料代码: 3,
  订单物料描述: 4,
  交货日期: 6,
  含税价: 7,
  可出货数量: 10,
}

type SheetRows = unknown[][]

function readSheetRows(sheet: XLSX.WorkSheet): SheetRows {
  // sheet['!ref'] 由 Excel 写入：若工作簿存在合并单元格或「中间列被全列空跳过」，
  // 实际单元格可能延伸到声明范围之外（实测：含可出货数量的采购订单明细
  // 真实数据到 AG88，但 !ref 仅为 A1:H88），sheet_to_json 默认按 !ref 切，
  // 会把表头和数据都裁成 8 列 → 全部可选列解析失败。
  // 这里从实际单元格重新计算 extents，再显式传给 sheet_to_json。
  let range: XLSX.Range | undefined
  let maxCol = -1
  let maxRow = -1
  for (const key of Object.keys(sheet)) {
    if (key.startsWith('!')) continue
    const addr = XLSX.utils.decode_cell(key)
    if (addr.c > maxCol) maxCol = addr.c
    if (addr.r > maxRow) maxRow = addr.r
  }
  if (maxCol >= 0 && maxRow >= 0) {
    range = { s: { c: 0, r: 0 }, e: { c: maxCol, r: maxRow } }
  }
  return XLSX.utils.sheet_to_json<unknown[]>(sheet, {
    header: 1,
    raw: false,
    defval: '',
    // header: 1 默认保留空行；显式声明以确保数组下标等于 Excel 行号 - 1。
    blankrows: true,
    ...(range ? { range } : {}),
  })
}

function buildHeaderMap(headerRow: unknown[]): Map<string, number> {
  const result = new Map<string, number>()
  headerRow.forEach((value, index) => {
    const header = cleanText(value)
    if (header && !result.has(header)) result.set(header, index)
  })
  return result
}

function parseNumberOrNull(value: unknown): number | null {
  const text = cleanText(value).replace(/,/g, '')
  if (!text) return null
  const parsed = Number.parseFloat(text)
  return Number.isNaN(parsed) ? null : parsed
}

function parseDateOrNull(value: unknown): string | null {
  if (value instanceof Date) {
    const parsed = dayjs(value)
    return parsed.isValid() ? parsed.format('YYYY-MM-DD') : null
  }

  const text = cleanText(value)
  if (!text) return null

  // raw:false 通常返回 Excel 已格式化的日期字符串；可解析时统一成 YYYY-MM-DD，
  // 无法识别时保留原值，让后续预览/匹配阶段能够展示源数据。
  const parsed = dayjs(text)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : text
}

function emptyResult(errors: string[] = []): ParsedPurchaseOrder {
  return { docNo: '', items: [], errors, warnings: [] }
}

/**
 * 解析采购订单 Excel，返回单据编号、有效明细以及错误/警告。
 *
 * @param buf ArrayBuffer（来自 el-upload onChange 的 raw.arrayBuffer()）
 */
export function parsePurchaseOrderExcel(buf: ArrayBuffer): ParsedPurchaseOrder {
  let workbook: XLSX.WorkBook
  try {
    workbook = XLSX.read(buf, { type: 'array', cellDates: true })
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    return emptyResult([`无法读取采购订单 Excel：${detail}`])
  }

  const result: ParsedPurchaseOrder = {
    docNo: '',
    items: [],
    errors: [],
    warnings: [],
  }

  const baseSheet = workbook.Sheets[BASE_SHEET_NAME]
  if (!baseSheet) {
    result.errors.push(`Excel 缺少 "${BASE_SHEET_NAME}" sheet`)
  } else {
    const rows = readSheetRows(baseSheet)
    const headerMap = buildHeaderMap(rows[HEADER_ROW_INDEX] ?? [])
    const docNoColumn = headerMap.get('单据编号')

    if (docNoColumn == null) {
      result.errors.push(`"${BASE_SHEET_NAME}" sheet 缺少必需列：单据编号`)
    } else {
      result.docNo = cleanText(rows[DATA_START_ROW_INDEX]?.[docNoColumn])
      if (!result.docNo) {
        result.errors.push(`"${BASE_SHEET_NAME}" sheet 第 5 行的单据编号为空`)
      }
    }
  }

  const detailSheet = workbook.Sheets[DETAIL_SHEET_NAME]
  if (!detailSheet) {
    result.errors.push(`Excel 缺少 "${DETAIL_SHEET_NAME}" sheet`)
    return result
  }

  const rows = readSheetRows(detailSheet)
  const headerMap = buildHeaderMap(rows[HEADER_ROW_INDEX] ?? [])
  const missingRequired = REQUIRED_DETAIL_HEADERS.filter(
    (header) => !headerMap.has(header),
  )
  if (missingRequired.length > 0) {
    result.errors.push(
      `"${DETAIL_SHEET_NAME}" sheet 缺少必需列：${missingRequired.join('、')}`,
    )
    return result
  }

  for (const header of [...REQUIRED_DETAIL_HEADERS, ...OPTIONAL_DETAIL_HEADERS]) {
    const actualColumn = headerMap.get(header)
    const expectedColumn = EXPECTED_DETAIL_COLUMNS[header]
    if (actualColumn != null && actualColumn !== expectedColumn) {
      result.warnings.push(
        `"${DETAIL_SHEET_NAME}" sheet 的「${header}」列位置与标准模板不一致，已按表头识别`,
      )
    }
  }

  for (const header of OPTIONAL_DETAIL_HEADERS) {
    if (!headerMap.has(header)) {
      result.warnings.push(
        `"${DETAIL_SHEET_NAME}" sheet 缺少可选列「${header}」，对应字段将留空`,
      )
    }
  }

  const lineNoColumn = headerMap.get('采购订单行号') ?? 0
  const deletedColumn = headerMap.get('已删除')
  const drawingNoColumn = headerMap.get('物料代码')!
  const nameColumn = headerMap.get('订单物料描述')!
  const deliveryDateColumn = headerMap.get('交货日期')
  const unitPriceColumn = headerMap.get('含税价')
  const shippableQtyColumn = headerMap.get('可出货数量')

  for (let rowIndex = DATA_START_ROW_INDEX; rowIndex < rows.length; rowIndex++) {
    const row = rows[rowIndex] ?? []
    const marker = cleanText(row[lineNoColumn])

    // 每个主明细后跟「计划行」子表头和序号为 1 的子数据；只保留数字主行。
    if (marker === '计划行' || marker === '1' || !/^\d+$/.test(marker)) continue

    const rowNo = rowIndex + 1
    const deleted = deletedColumn != null && cleanText(row[deletedColumn]) === '是'
    if (deleted) {
      result.warnings.push(`第 ${rowNo} 行（采购订单行号 ${marker}）已删除，已跳过`)
      continue
    }

    const drawingNo = cleanText(row[drawingNoColumn])
    const name = cleanText(row[nameColumn])
    if (!drawingNo && !name) continue

    result.items.push({
      rowNo,
      lineNo: marker,
      deleted: false,
      drawingNo,
      name,
      deliveryDate: deliveryDateColumn == null
        ? null
        : parseDateOrNull(row[deliveryDateColumn]),
      unitPrice: unitPriceColumn == null
        ? null
        : parseNumberOrNull(row[unitPriceColumn]),
      shippableQty: shippableQtyColumn == null
        ? null
        : parseNumberOrNull(row[shippableQtyColumn]),
    })
  }

  return result
}
