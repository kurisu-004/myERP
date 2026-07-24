// historicalPriceExcelParser (real fixture)
//
// 加载真实示例文件（历史价确认单）。走 node:fs 读本地路径，
// 不依赖 fetch / @types/node 全量安装（已有 devDependency）。
// 使用 import.meta.url 相对解析 → 不依赖机器绝对路径，CI / 其他 clone 也能跑。
// frontend/src/utils/__tests__/ → ../../../.. → repo 根 → docs/example/

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import * as XLSX from 'xlsx'

import { parseHistoricalPriceExcel } from '../historicalPriceExcelParser'
import type { BidRow } from '../bidExcelParser'

const FIXTURE_PATH = fileURLToPath(
  new URL(
    '../../../../docs/example/历史价确认单 (1).xlsx',
    import.meta.url,
  ),
)

describe('parseHistoricalPriceExcel (real fixture)', () => {
  it('parses docs/example/历史价确认单 (1).xlsx', () => {
    const buf = readFileSync(FIXTURE_PATH)
    const wb = XLSX.read(buf, { type: 'buffer' })
    const result = parseHistoricalPriceExcel(wb, '2026-07-13')

    // 真实 fixture：5 条数据行；其他 sheet（Sheet2/Sheet3/dropdownDataSheet...）不算数据
    expect(result.rows).toHaveLength(5)
    expect(result.errors).toEqual([])

    // 第 1 行：镀膜厂 / 程胜志 / E42DMMET45007101
    const r1 = result.rows[0] as BidRow
    expect(r1.rowNumber).toBe(2)
    expect(r1.applicantName).toBe('程胜志')
    expect(r1.deptName).toBe('镀膜厂')
    expect(r1.deptCode).toBe('')
    expect(r1.drawingNo).toBe('E42DMMET45007101')
    expect(r1.partName).toBe('断路器开关链接头')
    expect(r1.quantity).toBe(3)
    expect(r1.unitPrice).toBe(160)
    expect(r1.totalPrice).toBe(480)
    expect(r1.isUrgent).toBe(false)
    expect(r1.deliveryDays).toBe(14)
    expect(r1.plannedDeliveryDate).toBe('2026-07-27')
    expect(r1.designDrawingLabel).toBeNull()
    expect(r1.processTypeLabel).toBeNull()
    expect(r1.remarkText).toBeNull()

    // 设备部 / 陈宝红：数量 1500、单价 6、总价 9000（数字较大，验证精度）
    const r5 = result.rows[4] as BidRow
    expect(r5.applicantName).toBe('陈宝红')
    expect(r5.deptName).toBe('设备部')
    expect(r5.drawingNo).toBe('E4201BZPJXIM006')
    expect(r5.quantity).toBe(1500)
    expect(r5.unitPrice).toBe(6)
    expect(r5.totalPrice).toBe(9000)
    expect(r5.deliveryDays).toBe(14)

    // 税率列被丢弃：所有 row 都不应带 warnings、且与税率解析相关字段都为 null
    for (const r of result.rows as BidRow[]) {
      expect(r.warnings).toEqual([])
      expect(r.designDrawingLabel).toBeNull()
      expect(r.processTypeLabel).toBeNull()
      expect(r.remarkText).toBeNull()
      expect(r.isUrgent).toBe(false)
    }

    // 申请部门多样性：5 条数据覆盖 镀膜厂 / 八厂 / 六厂 / 设备部
    const deptNames = new Set(result.rows.map((r) => r.deptName))
    expect(deptNames.size).toBeGreaterThan(1)
  })
})