// bidExcelParser 单元测试。
//
// 用 XLSX.utils.json_to_sheet + XLSX.utils.book_new 构造内存 workbook，
// 走 XLSX.utils.sheet_to_json({raw:false}) 的真实路径（与浏览器解析一致）。
//
// 注意：货物(劳务)名称 / 方案/设计图纸 等含特殊字符的列名必须用 quoted key，
// 与 Excel 表头严格一致。

import { describe, it, expect } from 'vitest'
import * as XLSX from 'xlsx'

import { parseBidExcel, type BidRow } from '../bidExcelParser'

const ALL_HEADERS = [
  '序号',
  '申请人',
  '申请人所在一级部门',
  '申请人所在一级部门名称',
  '方案/设计图纸',
  '文件名', // 第 6 列无表头（保留位）
  '图片',
  '物料编号',
  '货物(劳务)名称',
  '规格型号',
  '品牌',
  '紧急状态',
  '单位',
  '计划数量',
  '附件',
  '备注',
  '加工类型',
  '含税单价',
  '含税价格',
  '税码',
  '不含税价格',
  '预估交期天数',
  '报价反馈',
] as const

type RowDict = Record<string, unknown>

function buildWorkbook(
  rows: RowDict[],
  sheetName = '招标项目-标的',
): XLSX.WorkBook {
  const ws = XLSX.utils.json_to_sheet(rows, { header: [...ALL_HEADERS] })
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, sheetName)
  return wb
}

const TODAY = '2026-07-13'

describe('parseBidExcel', () => {
  it('parses a valid 5-row bid sample', () => {
    const wb = buildWorkbook([
      {
        申请人: '黄启福',
        申请人所在一级部门: 'F05',
        申请人所在一级部门名称: '五厂',
        物料编号: 'E42ZZL521335302',
        '货物(劳务)名称': '先导半成品下压机构4-5(改)',
        紧急状态: '正常',
        计划数量: 10,
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: '蒋燕梅',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'E42103WXJ040500',
        '货物(劳务)名称': 'XM-F铜头柱塑料',
        紧急状态: '急件',
        计划数量: 50,
        含税单价: 12.5,
        预估交期天数: 7,
      },
      {
        申请人: '张三',
        申请人所在一级部门: 'F05',
        申请人所在一级部门名称: '五厂',
        物料编号: 'E42ZZL521335401',
        '货物(劳务)名称': '先导半成品下压机构5-5',
        紧急状态: '非常紧急',
        计划数量: 5,
        含税单价: 0,
        预估交期天数: 5,
      },
      {
        申请人: '李四',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'E42103WXJ041400',
        '货物(劳务)名称': 'HH-固化3从动轮',
        紧急状态: '',
        计划数量: 3,
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: '王五',
        申请人所在一级部门: 'F06',
        申请人所在一级部门名称: '六厂',
        物料编号: 'E42ZZL521335999',
        '货物(劳务)名称': 'X 部件',
        紧急状态: '未知紧急', // 不在白名单
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
    ])

    const result = parseBidExcel(wb, TODAY)

    expect(result.errors).toEqual([])
    expect(result.rows).toHaveLength(5)

    // row 1
    const r1 = result.rows[0] as BidRow
    expect(r1.rowNumber).toBe(2) // 1-based + header
    expect(r1.applicantName).toBe('黄启福')
    expect(r1.deptCode).toBe('F05')
    expect(r1.deptName).toBe('五厂')
    expect(r1.drawingNo).toBe('E42ZZL521335302')
    expect(r1.partName).toBe('先导半成品下压机构4-5(改)')
    expect(r1.quantity).toBe(10)
    expect(r1.isUrgent).toBe(false)
    expect(r1.deliveryDays).toBe(14)
    expect(r1.plannedDeliveryDate).toBe('2026-07-27')
    expect(r1.totalPrice).toBe(0)
    expect(r1.warnings).toEqual([])

    // row 2: 含税单价 12.5 × 50 = 625
    const r2 = result.rows[1] as BidRow
    expect(r2.isUrgent).toBe(true)
    expect(r2.plannedDeliveryDate).toBe('2026-07-20')
    expect(r2.unitPrice).toBe(12.5)
    expect(r2.totalPrice).toBe(625)
    expect(r2.quantity).toBe(50)

    // row 3: 非常紧急 → true
    expect((result.rows[2] as BidRow).isUrgent).toBe(true)
    expect((result.rows[2] as BidRow).plannedDeliveryDate).toBe('2026-07-18')

    // row 4: 空 紧急状态 → 默认 false，无错误
    expect((result.rows[3] as BidRow).isUrgent).toBe(false)
    expect((result.rows[3] as BidRow).warnings).toEqual([])

    // row 5: 紧急状态「未知紧急」未识别 → 默认 false + warning
    const r5 = result.rows[4] as BidRow
    expect(r5.isUrgent).toBe(false)
    expect(r5.warnings).toContain('紧急状态「未知紧急」未识别，按正常处理')
  })

  it('skips trailing empty rows', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
      {}, // 全空
      {}, // 全空
    ])
    const result = parseBidExcel(wb, TODAY)
    expect(result.rows).toHaveLength(1)
    expect(result.errors).toEqual([])
  })

  it('flags error rows for missing required fields', () => {
    const wb = buildWorkbook([
      {
        // 缺申请人
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        // 缺 物料编号
        '货物(劳务)名称': 'P2',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D3',
        // 缺 货物名称
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    expect(result.rows).toHaveLength(3) // 仍 parse 出 3 行
    expect(result.errors).toHaveLength(3)
    expect(result.errors[0]?.message).toMatch(/申请人不能为空/)
    expect(result.errors[1]?.message).toMatch(/物料编号不能为空/)
    expect(result.errors[2]?.message).toMatch(/货物\(劳务\)名称不能为空/)
    expect(result.errors[0]?.rowNumber).toBe(2)
  })

  it('flags error rows for invalid quantity / delivery days', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 'abc', // 非整数
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D2',
        '货物(劳务)名称': 'P2',
        紧急状态: '正常',
        计划数量: 0, // 非正
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D3',
        '货物(劳务)名称': 'P3',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: '', // 空
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D4',
        '货物(劳务)名称': 'P4',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 0, // 非正
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    expect(result.errors).toHaveLength(4)
    expect(result.errors[0]?.message).toMatch(/计划数量必须为正整数/)
    expect(result.errors[1]?.message).toMatch(/计划数量必须为正整数/)
    expect(result.errors[2]?.message).toMatch(/预估交期天数必须为正整数/)
    expect(result.errors[3]?.message).toMatch(/预估交期天数必须为正整数/)
  })

  it('warns (does not block) for duplicate drawing_no within the batch', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'DUP',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'DUP', // 重复
        '货物(劳务)名称': 'P2',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    expect(result.errors).toEqual([])
    expect(result.rows).toHaveLength(2)
    expect((result.rows[1] as BidRow).warnings).toContain('本批次有重复图号')
  })

  it('clamps negative unit price to 0 with a warning', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 5,
        含税单价: -1,
        预估交期天数: 14,
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    expect(result.errors).toEqual([])
    const r = result.rows[0] as BidRow
    expect(r.unitPrice).toBe(0)
    expect(r.totalPrice).toBe(0)
    expect(r.warnings).toContain('含税单价为负数，已按 0 处理')
  })

  it('extracts first fileName from 方案/设计图纸 JSON cell', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        '方案/设计图纸':
          '[{"accessoryId":"abc","fileName":"abc.PDF"},{"fileName":"def.PDF"}]',
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    expect((result.rows[0] as BidRow).designDrawingLabel).toBe('abc.PDF')
  })

  it('handles truncated JSON cell gracefully (returns null)', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        '方案/设计图纸': '[{"accessoryId":"abc"', // 被截断
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    expect((result.rows[0] as BidRow).designDrawingLabel).toBeNull()
  })

  it('computes planned_delivery_date = today + deliveryDays', () => {
    const wb = buildWorkbook([
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D1',
        '货物(劳务)名称': 'P1',
        紧急状态: '正常',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 1, // 跨日
      },
      {
        申请人: 'A',
        申请人所在一级部门: 'F01',
        申请人所在一级部门名称: '一厂',
        物料编号: 'D2',
        '货物(劳务)名称': 'P2',
        紧急状态: '急件',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 7,
      },
    ])
    const result = parseBidExcel(wb, '2026-12-30') // 跨年
    expect((result.rows[0] as BidRow).plannedDeliveryDate).toBe('2026-12-31')
    expect((result.rows[1] as BidRow).plannedDeliveryDate).toBe('2027-01-06')
  })

  it('throws when sheet name is wrong', () => {
    const wb = buildWorkbook(
      [
        {
          申请人: 'A',
          物料编号: 'D1',
          '货物(劳务)名称': 'P1',
          紧急状态: '正常',
          计划数量: 1,
          含税单价: 0,
          预估交期天数: 14,
          申请人所在一级部门: 'F01',
          申请人所在一级部门名称: '一厂',
        },
      ],
      'wrong-sheet',
    )
    expect(() => parseBidExcel(wb, TODAY)).toThrow(/招标项目-标的/)
  })

  it('throws when required headers are missing', () => {
    // 不走 buildWorkbook helper（helper 强制注入完整表头），手动建一个
    // 真正缺列的 sheet —— 表头只有 2 列，7 列必需列都缺。
    const ws = XLSX.utils.json_to_sheet([
      {
        申请人: 'A',
        物料编号: 'D1',
      },
    ])
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, '招标项目-标的')
    expect(() => parseBidExcel(wb, TODAY)).toThrow(/Excel 缺少必需列/)
  })

  it('handles empty workbook gracefully', () => {
    const wb = buildWorkbook([])
    const result = parseBidExcel(wb, TODAY)
    expect(result.rows).toEqual([])
    expect(result.warnings).toContain('Excel 没有数据行')
  })

  it('strips whitespace from text fields', () => {
    const wb = buildWorkbook([
      {
        申请人: '  张三  ',
        申请人所在一级部门: ' F05 ',
        申请人所在一级部门名称: ' 五厂 ',
        物料编号: ' D1 ',
        '货物(劳务)名称': ' P1 ',
        紧急状态: ' 正常 ',
        计划数量: 1,
        含税单价: 0,
        预估交期天数: 14,
      },
    ])
    const result = parseBidExcel(wb, TODAY)
    const r = result.rows[0] as BidRow
    expect(r.applicantName).toBe('张三')
    expect(r.deptCode).toBe('F05')
    expect(r.deptName).toBe('五厂')
    expect(r.drawingNo).toBe('D1')
    expect(r.partName).toBe('P1')
  })
})
