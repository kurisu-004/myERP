// xlsxParseUtils.ts
//
// 抽出 bidExcelParser / historicalPriceExcelParser 共用的纯函数 helper。
// 不依赖任何外部业务模块，零网络 / 零 DOM，方便单元测试直接 import。
//
// 这些 helper 原本是 bidExcelParser.ts 的私有函数，2026-07-24
// 因为新增「历史价确认单」parser 需要复用而抽出到这里。
//
// 既有调用方（bidExcelParser.ts）已改为 import 此文件；保留原函数名以
// 保持可读性。

/** `null` / `undefined` / 各种 falsy 都安全 trim；非字符串先转字符串。 */
export function cleanText(value: unknown): string {
  if (value == null) return ''
  return String(value).trim()
}

/** 安全解析整数；空串/非法 → 返回 fallback；fallback 默认 null。
 *  Excel 单元格常含千分位逗号（如 "1,500"）和货币符号，先剥离再 Number。
 */
export function parseIntSafe(value: unknown, fallback: number | null = null): number | null {
  if (value == null || value === '') return fallback
  const s = String(value).trim().replace(/,/g, '')
  const n = Number(s)
  if (!Number.isFinite(n) || !Number.isInteger(n)) return fallback
  return n
}

/** 解析十进制数；空串/非法 → 0；不应判负数的场景（如含税单价）由 caller 校验。 */
export function parseDecimal(value: unknown): number {
  if (value == null || value === '') return 0
  const s = String(value).trim().replace(/,/g, '')
  const n = Number(s)
  if (!Number.isFinite(n)) return 0
  return n
}

/** 解析十进制数；空串/非法 → null（区别于 parseDecimal：保留 null 语义）。 */
export function parseDecimalOrNull(value: unknown): number | null {
  if (value == null || value === '') return null
  const s = String(value).trim().replace(/,/g, '')
  const n = Number(s)
  if (!Number.isFinite(n)) return null
  return n
}

/** `YYYY-MM-DD + days` → `YYYY-MM-DD`（UTC 算术避免夏令时踩坑）。 */
export function addDays(yyyy_mm_dd: string, days: number): string {
  const [y, m, d] = yyyy_mm_dd.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  date.setUTCDate(date.getUTCDate() + days)
  return [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, '0'),
    String(date.getUTCDate()).padStart(2, '0'),
  ].join('-')
}