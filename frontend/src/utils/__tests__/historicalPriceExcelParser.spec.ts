// historicalPriceExcelParser 单元测试。
//
// 用 XLSX.utils.json_to_sheet + XLSX.utils.book_new 构造内存 workbook，
// 走 XLSX.utils.sheet_to_json({raw:false}) 的真实路径（与浏览器解析一致）。
//
// 与 bidExcelParser.spec.ts 用同一套模式：构造 → 解析 → 断言。
// 列名固定顺序确保 sheet_to_json header 模式能识别 quoted key。

import { describe, it, expect } from 'vitest'
import * as XLSX from 'xlsx'

import { parseHistoricalPriceExcel } from '../historicalPriceExcelParser'
import type { BidRow } from '../bidExcelParser'

// 列顺序与 docs/example/历史价确认单 (1).xlsx 一致；
// 实际 PDF Tab 上前端只识别 8 个必需列，税率列允许缺失或保留。
const ALL_HEADERS = [
  '申请部门',
  '申请人',
  '交期(天)',
  '物料编号',
  '物料名称',
  '采购数量',
  '含税单价',
  '税率',
  '含税价格',
] as const

type RowDict = Record<string, unknown>

function buildWorkbook(
  rows: RowDict[],
  sheetName = '历史价确认单明细',
): XLSX.WorkBook {
  const ws = XLSX.utils.json_to_sheet(rows, { header: [...ALL_HEADERS] })
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, sheetName)
  return wb
}

const TODAY = '2026-07-13'

describe('parseHistoricalPriceExcel', () => {
  it('parses a valid 5-row historical-price sample', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: '程胜志',
        '交期(天)': 14,
        物料编号: 'E42DMMET45007101',
        物料名称: '断路器开关链接头',
        采购数量: 3,
        含税单价: 160,
        税率: '13%',
        含税价格: 480,
      },
      {
        申请部门: '八厂',
        申请人: '胡明',
        '交期(天)': 7,
        物料编号: 'E42FXJMT01351101',
        物料名称: '相机支架1',
        采购数量: 1,
        含税单价: 105,
        税率: '13%',
        含税价格: 105,
      },
      {
        申请部门: '六厂',
        申请人: '谢岩国',
        '交期(天)': 14,
        物料编号: 'E42HJWLD10012101',
        物料名称: '扁条收框档条',
        采购数量: 2,
        含税单价: 130,
        税率: '13%',
        含税价格: 260,
      },
      {
        申请部门: '八厂',
        申请人: '余佳星',
        '交期(天)': 5,
        物料编号: 'E42803FZJ052100',
        物料名称: '1167分选底座',
        采购数量: 2,
        含税单价: 265,
        税率: '13%',
        含税价格: 530,
      },
      {
        申请部门: '设备部',
        申请人: '陈宝红',
        '交期(天)': 14,
        物料编号: 'E4201BZPJXIM006',
        物料名称: '单层电刷2mm（普通不带钩）',
        采购数量: 1500,
        含税单价: 6,
        税率: '13%',
        含税价格: 9000,
      },
    ])

    const result = parseHistoricalPriceExcel(wb, TODAY)

    expect(result.errors).toEqual([])
    expect(result.rows).toHaveLength(5)

    const r1 = result.rows[0] as BidRow
    expect(r1.rowNumber).toBe(2)
    expect(r1.applicantName).toBe('程胜志')
    expect(r1.deptName).toBe('镀膜厂')
    expect(r1.deptCode).toBe('') // 历史价格式无一级部门代码列
    expect(r1.drawingNo).toBe('E42DMMET45007101')
    expect(r1.partName).toBe('断路器开关链接头')
    expect(r1.quantity).toBe(3)
    expect(r1.unitPrice).toBe(160)
    expect(r1.totalPrice).toBe(480) // 优先用文件给的「含税价格」
    expect(r1.isUrgent).toBe(false) // 格式无紧急状态列 → 恒为 false
    expect(r1.deliveryDays).toBe(14)
    expect(r1.plannedDeliveryDate).toBe('2026-07-27')
    expect(r1.designDrawingLabel).toBeNull()
    expect(r1.processTypeLabel).toBeNull()
    expect(r1.remarkText).toBeNull()
    expect(r1.warnings).toEqual([])

    // 税率被丢弃，验证最后一个 row 也没有溢出字段
    const r5 = result.rows[4] as BidRow
    expect(r5.totalPrice).toBe(9000)
    expect(r5.applicantName).toBe('陈宝红')
    expect(r5.deptName).toBe('设备部')
  })

  it('skips trailing empty rows', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: '程胜志',
        '交期(天)': 14,
        物料编号: 'E42DMMET45007101',
        物料名称: '断路器开关链接头',
        采购数量: 3,
        含税单价: 160,
        含税价格: 480,
      },
      {},
      {},
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect(result.rows).toHaveLength(1)
    expect(result.errors).toEqual([])
  })

  it('flags error rows for missing required fields', () => {
    const wb = buildWorkbook([
      {
        // 缺申请人
        申请部门: '镀膜厂',
        '交期(天)': 14,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
      {
        申请部门: '八厂',
        申请人: 'A',
        '交期(天)': 14,
        // 缺 物料编号
        物料名称: 'P2',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
      {
        申请部门: '六厂',
        申请人: 'B',
        '交期(天)': 14,
        物料编号: 'D3',
        // 缺 物料名称
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect(result.rows).toHaveLength(3)
    expect(result.errors).toHaveLength(3)
    expect(result.errors[0]?.message).toMatch(/申请人不能为空/)
    expect(result.errors[1]?.message).toMatch(/物料编号不能为空/)
    expect(result.errors[2]?.message).toMatch(/物料名称不能为空/)
    expect(result.errors[0]?.rowNumber).toBe(2)
  })

  it('flags error rows for invalid quantity / delivery days', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 'abc', // 非整数
        含税单价: 100,
        含税价格: 0,
      },
      {
        申请部门: '八厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'D2',
        物料名称: 'P2',
        采购数量: 0, // 非正
        含税单价: 100,
        含税价格: 0,
      },
      {
        申请部门: '六厂',
        申请人: 'A',
        '交期(天)': '', // 空
        物料编号: 'D3',
        物料名称: 'P3',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
      {
        申请部门: '设备部',
        申请人: 'A',
        '交期(天)': 0, // 非正
        物料编号: 'D4',
        物料名称: 'P4',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect(result.errors).toHaveLength(4)
    expect(result.errors[0]?.message).toMatch(/采购数量必须为正整数/)
    expect(result.errors[1]?.message).toMatch(/采购数量必须为正整数/)
    expect(result.errors[2]?.message).toMatch(/交期\(天\)必须为正整数/)
    expect(result.errors[3]?.message).toMatch(/交期\(天\)必须为正整数/)
  })

  it('warns (does not block) for duplicate drawing_no within the batch', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'DUP',
        物料名称: 'P1',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
      {
        申请部门: '八厂',
        申请人: 'B',
        '交期(天)': 14,
        物料编号: 'DUP',
        物料名称: 'P2',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect(result.errors).toEqual([])
    expect(result.rows).toHaveLength(2)
    expect((result.rows[1] as BidRow).warnings).toContain('本批次有重复图号')
  })

  it('clamps negative unit price to 0 with a warning', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 5,
        含税单价: -1,
        含税价格: 500,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect(result.errors).toEqual([])
    const r = result.rows[0] as BidRow
    expect(r.unitPrice).toBe(0)
    expect(r.totalPrice).toBe(500) // 含税价格优先于 unitPrice * quantity
    expect(r.warnings).toContain('含税单价为负数，已按 0 处理')
  })

  it('trims whitespace in text fields', () => {
    const wb = buildWorkbook([
      {
        申请部门: '  镀膜厂  ',
        申请人: '  程胜志  ',
        '交期(天)': 14,
        物料编号: '  E42DMMET45007101  ',
        物料名称: '  断路器开关链接头  ',
        采购数量: 3,
        含税单价: 160,
        含税价格: 480,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    const r = result.rows[0] as BidRow
    expect(r.applicantName).toBe('程胜志')
    expect(r.deptName).toBe('镀膜厂')
    expect(r.drawingNo).toBe('E42DMMET45007101')
    expect(r.partName).toBe('断路器开关链接头')
  })

  it('computes plannedDeliveryDate as today + deliveryDays', () => {
    const wb = buildWorkbook([
      {
        申请部门: '八厂',
        申请人: 'A',
        '交期(天)': 5,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
      {
        申请部门: '六厂',
        申请人: 'B',
        '交期(天)': 30,
        物料编号: 'D2',
        物料名称: 'P2',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect((result.rows[0] as BidRow).plannedDeliveryDate).toBe('2026-07-18')
    expect((result.rows[1] as BidRow).plannedDeliveryDate).toBe('2026-08-12')
  })

  it('always sets isUrgent to false (no 紧急状态 column in this format)', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 1,
        含税单价: 100,
        含税价格: 100,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    const r = result.rows[0] as BidRow
    expect(r.isUrgent).toBe(false)
    expect(r.warnings).not.toContain(expect.stringMatching(/紧急/))
  })

  it('falls back to unitPrice * quantity when 含税价格 is missing or invalid', () => {
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 3,
        含税单价: 160,
        含税价格: '', // 缺失
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    const r = result.rows[0] as BidRow
    expect(r.totalPrice).toBe(480) // 160 * 3
  })

  it('drops 税率 column silently (no extra field in BidRow)', () => {
    // 税率值千奇百怪都不会进 BidRow / 不会污染 warnings / 不会出 row errors
    const wb = buildWorkbook([
      {
        申请部门: '镀膜厂',
        申请人: 'A',
        '交期(天)': 14,
        物料编号: 'D1',
        物料名称: 'P1',
        采购数量: 1,
        含税单价: 100,
        税率: '99%XYZ', // 任意值
        含税价格: 100,
      },
    ])
    const result = parseHistoricalPriceExcel(wb, TODAY)
    expect(result.errors).toEqual([])
    expect(result.warnings).toEqual([])
    const r = result.rows[0] as BidRow
    expect(r.warnings).toEqual([])
  })

  it('throws when sheet name is wrong', () => {
    const wb = buildWorkbook(
      [
        {
          申请部门: '镀膜厂',
          申请人: 'A',
          '交期(天)': 14,
          物料编号: 'D1',
          物料名称: 'P1',
          采购数量: 1,
          含税单价: 100,
          含税价格: 100,
        },
      ],
      '错误的sheet名',
    )
    expect(() => parseHistoricalPriceExcel(wb, TODAY)).toThrow(/历史价确认单/)
  })

  it('throws when required columns are missing', () => {
    // 构造只有部分列的 workbook
    const ws = XLSX.utils.json_to_sheet(
      [
        {
          申请人: 'A',
          物料编号: 'D1',
        },
      ],
      { header: ['申请人', '物料编号'] },
    )
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, '历史价确认单明细')
    expect(() => parseHistoricalPriceExcel(wb, TODAY)).toThrow(
      /缺少必需列/,
    )
  })
})