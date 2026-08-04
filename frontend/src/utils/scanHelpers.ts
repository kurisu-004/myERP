// 三页扫码台共享 helper：扫错页提示 + 同条码多批次匹配。
// 把这两件事从每页 Vue 文件里抽出来，修正后保持三页一致，并方便以后改文案。

import { h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPartBySerial, type PartItem } from '@/api/parts'

/**
 * 在给定的行列表里按 serial_no || drawing_no 找全部匹配。
 * 同一工件的多个批次（`t_part_batch` 拆分后）会得到多个匹配行——返回数组，
 * 调用方按 0 / 1 / 多分三路。
 */
export function findAllByCode(rows: PartItem[], code: string): PartItem[] {
  return rows.filter(
    (p) =>
      (p.serial_no && p.serial_no === code) ||
      (p.drawing_no && p.drawing_no === code),
  )
}

/**
 * 仅按 serial_no 严格匹配（送货单 picker 用：barcode = serial_no）。
 * 与 `findAllByCode` 不同，这里不匹配 drawing_no——picker 的候选行已按
 * INSPECTION / READY_TO_SHIP 过滤，扫码命中即代表「这个工单的某个批次在候选里」；
 * 0 命中直接调 `findPartBySerialAndPrompt` 显示当前位置。
 *
 * 泛型 `T extends { serial_no: string | null }`：兼容 `PartItem` 和
 * `DeliveryNoteCandidatePart`（后者也有 `serial_no` 字段）。
 */
export function findBySerialNo<T extends { serial_no: string | null }>(
  rows: T[],
  code: string,
): T[] {
  const want = code.trim()
  if (!want) return []
  return rows.filter((r) => r.serial_no === want)
}

/**
 * 调 `GET /parts/by-serial/{serial_no}`；成功时阻塞弹窗（`ElMessageBox.alert`）
 * 显示该零件的当前位置/持有人/状态/下一工序，提示工人「可能不在本工序」；
 * 找不到（404 等）→ `ElMessage.warning` 一行。
 *
 * 关键：message 传 VNode（不是拼好的 `lines.join('\n')`）—— Element Plus 对 plain
 * string 的 message **不会**把 `\n` 渲染成换行（参见项目
 * /Users/ren/.claude/plans/scanpickparts-vue-scanreturnparts-vue-sc-luminous-crown.md）。
 * VNode 路径下每行一个 `<div>`，自然换行。
 *
 * `message: VNode` 类型 Element Plus 2.14.x 收口不严，`as any` 是已知 workaround。
 */
export async function findPartBySerialAndPrompt(code: string): Promise<void> {
  let part: PartItem
  try {
    part = await getPartBySerial(code)
  } catch (e) {
    ElMessage.warning(
      `未找到条码 ${code} 对应的零件：${(e as Error).message ?? ''}`,
    )
    return
  }
  const where = part.current_holder_display ?? part.location ?? '未知位置'
  const rows: { label: string; value: string }[] = [
    { label: '条码', value: part.serial_no ?? part.drawing_no ?? code },
    { label: '名称', value: part.name },
    {
      label: '批次',
      value: part.batch_no == null ? '—' : String(part.batch_no),
    },
    { label: '状态', value: part.status },
    { label: '当前所在', value: where },
  ]
  if (part.next_process_name) {
    rows.push({ label: '下一工序', value: part.next_process_name })
  }
  const messageVNode = h(
    'div',
    {
      class: 'scan-mismatch-msg',
      style: 'line-height: 1.8; font-size: 14px;',
    },
    [
      ...rows.map((r) =>
        h('div', { class: 'row' }, [
          h(
            'span',
            {
              class: 'lbl',
              style:
                'color: #909399; display: inline-block; min-width: 70px; margin-right: 8px;',
            },
            `${r.label}：`,
          ),
          h('span', { class: 'val' }, r.value),
        ]),
      ),
      h(
        'div',
        {
          class: 'hint',
          style:
            'margin-top: 10px; padding-top: 8px; border-top: 1px dashed #e6a23c; color: #b88230;',
        },
        `提示：该零件不在本工序，请到「${where}」继续流程。`,
      ),
    ],
  )
  await ElMessageBox.alert(
    messageVNode as unknown as string,
    '该零件当前不在本工序',
    {
      type: 'warning',
      confirmButtonText: '知道了',
    },
  )
}
