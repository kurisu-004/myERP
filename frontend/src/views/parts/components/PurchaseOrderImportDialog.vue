<!--
  PurchaseOrderImportDialog.vue

  /parts? 采购订单 Excel 导入对话框。
  流程：上传 .xlsx → parsePurchaseOrderExcel → matchPartsByExcelItems
       → 预览（可改订单号 / 交期 / 跳过）→ batchUpdatePartsOrderInfo

  设计要点：
  - 解析纯函数 + 后端 match API：行 = 一个 Excel 物料行；展示匹配零件 + 警告。
  - 默认跳过未匹配行；未匹配行标红。
  - 后端 OCC（version）由后端从 match 返回，前端原样回传。
  - 批量更新失败时，保留对话框，用 failedRows 标红失败行。
-->

<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    :width="dlg.width.value"
    :top="dlg.top.value"
    :fullscreen="dlg.fullscreen.value"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="onModelValueChange"
  >
    <!-- 顶部：单据号 + 上传 -->
    <div class="dlg-top">
      <div class="doc-no">
        <span class="label">单据号：</span>
        <el-tag v-if="docNo" type="primary" effect="plain" size="large">{{ docNo }}</el-tag>
        <el-tag v-else type="info" effect="plain" size="large" disable-transitions>
          尚未上传
        </el-tag>
      </div>
      <el-upload
        ref="uploadRef"
        :auto-upload="false"
        :show-file-list="false"
        accept=".xlsx,.xls"
        :limit="1"
        :on-change="onFileChange"
        :on-exceed="onExceed"
      >
        <el-button :loading="parsing" type="primary" plain>
          <el-icon><Upload /></el-icon>
          <span>{{ docNo ? '重新上传 Excel' : '选择 Excel 文件' }}</span>
        </el-button>
      </el-upload>
    </div>

    <!-- 解析错误 -->
    <el-alert
      v-for="(msg, idx) in parseErrors"
      :key="`err-${idx}`"
      :title="msg"
      type="error"
      :closable="false"
      show-icon
      class="alert-bar"
    />

    <!-- 解析警告 -->
    <el-alert
      v-for="(msg, idx) in parseWarnings"
      :key="`warn-${idx}`"
      :title="msg"
      type="warning"
      :closable="false"
      show-icon
      class="alert-bar"
    />

    <!-- 匹配/更新错误 -->
    <el-alert
      v-for="(msg, idx) in matchErrors"
      :key="`match-err-${idx}`"
      :title="msg"
      type="error"
      :closable="false"
      show-icon
      class="alert-bar"
    />

    <!-- 预览表 -->
    <el-table
      v-if="previewRows.length > 0"
      :data="previewRows"
      :row-class-name="rowClassName"
      :max-height="500"
      size="small"
      border
      stripe
    >
      <el-table-column prop="rowNo" label="Excel 行号" width="92" align="center" />

      <el-table-column label="物料代码" min-width="170" align="center">
        <template #default="{ row }">
          <span class="mono">{{ (row as PreviewRow).excelDrawingNo || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="描述" min-width="170" align="center">
        <template #default="{ row }">
          <span>{{ (row as PreviewRow).excelName || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="matchTagType((row as PreviewRow).matchType)" effect="light" size="small">
            {{ matchTagText((row as PreviewRow).matchType) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="目标零件" min-width="200" align="center">
        <template #default="{ row }">
          <template v-if="(row as PreviewRow).part">
            <div class="target-line">
              <span class="mono">{{ (row as PreviewRow).part!.drawing_no || '—' }}</span>
              <span>{{ (row as PreviewRow).part!.name }}</span>
            </div>
            <div v-if="(row as PreviewRow).part!.assembly_name" class="assembly-line muted">
              所属装配件：{{ (row as PreviewRow).part!.assembly_name }}
            </div>
            <div v-if="(row as PreviewRow).parts.length > 1" class="muted small">
              共 {{ (row as PreviewRow).parts.length }} 个匹配，仅更新第一个
            </div>
          </template>
          <span v-else class="muted">未匹配</span>
        </template>
      </el-table-column>

      <el-table-column label="现订单号 → 新订单号" min-width="220" align="center">
        <template #default="{ row }">
          <div class="edit-stack">
            <span class="cur mono">{{ (row as PreviewRow).part?.order_no ?? '—' }}</span>
            <el-input
              v-model="(row as PreviewRow).orderNo"
              size="small"
              :disabled="!(row as PreviewRow).part || (row as PreviewRow).skip"
              placeholder="新订单号"
            />
          </div>
        </template>
      </el-table-column>

      <el-table-column label="现交期 → 新交期" min-width="240" align="center">
        <template #default="{ row }">
          <div class="edit-stack">
            <span class="cur mono">{{ (row as PreviewRow).part?.system_delivery_date ?? '—' }}</span>
            <el-date-picker
              v-model="(row as PreviewRow).systemDeliveryDate"
              type="date"
              value-format="YYYY-MM-DD"
              :disabled="!(row as PreviewRow).part || (row as PreviewRow).skip"
              size="small"
              style="width: 160px"
              placeholder="新系统交期"
            />
          </div>
        </template>
      </el-table-column>

      <el-table-column label="警告" min-width="160" align="center">
        <template #default="{ row }">
          <template v-if="(row as PreviewRow).warnings.length > 0">
            <el-tooltip
              :content="(row as PreviewRow).warnings.join('；')"
              placement="top"
              :show-after="200"
            >
              <el-tag type="warning" effect="plain" size="small">
                {{ (row as PreviewRow).warnings.length }} 条
              </el-tag>
            </el-tooltip>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="跳过" width="70" align="center" fixed="right">
        <template #default="{ row }">
          <el-checkbox
            v-model="(row as PreviewRow).skip"
            :disabled="!(row as PreviewRow).part"
          />
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-else-if="!parsing"
      description="尚未上传 Excel，或解析后无有效数据"
      :image-size="80"
    />

    <template #footer>
      <div class="dlg-footer">
        <span class="preview-stats">
          共 {{ previewRows.length }} 行，匹配 {{ matchedCount }} 个零件，
          未匹配 {{ unmatchedCount }} 行，将更新 {{ effectiveCount }} 个
        </span>
        <el-button @click="onCancel">取消</el-button>
        <el-button
          type="primary"
          :disabled="effectiveCount === 0"
          :loading="submitting"
          @click="onConfirm"
        >
          更新 {{ effectiveCount }} 个
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { UploadFile, UploadRawFile } from 'element-plus'
import { ElMessage, ElMessageBox } from 'element-plus'

import { useDialogSize } from '@/composables/useDialogSize'
import {
  batchUpdatePartsOrderInfo,
  matchPartsByExcelItems,
  type PartBatchOrderInfoMatchItem,
  type PartBatchOrderInfoMatchResult,
  type PartBatchOrderInfoUpdateItem,
  type PartMatchInfo,
} from '@/api/parts'
import {
  parsePurchaseOrderExcel,
  type PurchaseOrderExcelItem,
} from '@/utils/purchaseOrderExcelParser'

// ============================================================
// Props / Emits
// ============================================================

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  success: []
}>()

const dlg = useDialogSize({ desktopWidth: 1100, fullscreenOnMobile: true })

const dialogTitle = computed(() => '解析系统交期和订单号')

// ============================================================
// 行模型
// ============================================================

interface PreviewRow {
  rowNo: number
  lineNo: string
  excelDrawingNo: string
  excelName: string
  matchType: 'PART_CODE' | 'PART_NAME' | 'ASSEMBLY_CODE' | 'ASSEMBLY_NAME' | 'NONE'
  part: PartMatchInfo | null
  parts: PartMatchInfo[]
  warnings: string[]
  unmatched: boolean
  hasWarnings: boolean
  orderNo: string
  systemDeliveryDate: string | null
  skip: boolean
}

// ============================================================
// 状态
// ============================================================

const uploadRef = ref<{ clearFiles?: () => void } | null>(null)
const docNo = ref('')
const parseErrors = ref<string[]>([])
const parseWarnings = ref<string[]>([])
const matchErrors = ref<string[]>([])
const previewRows = ref<PreviewRow[]>([])
const parsing = ref(false)
const submitting = ref(false)
/** part_id → 失败原因。用于标红失败行（不阻塞再次提交）。 */
const failedRows = ref<Map<string, string>>(new Map())

// ============================================================
// Computed
// ============================================================

const matchedCount = computed(
  () => previewRows.value.filter((r) => r.part !== null).length,
)
const unmatchedCount = computed(
  () => previewRows.value.filter((r) => r.part === null).length,
)

interface EffectiveItem {
  part_id: string
  version: number
  order_no: string
  system_delivery_date: string | null
}

const effectiveItems = computed<EffectiveItem[]>(() => {
  const out: EffectiveItem[] = []
  for (const r of previewRows.value) {
    if (r.skip) continue
    if (r.part === null) continue
    if (failedRows.value.has(r.part.part_id)) continue
    out.push({
      part_id: r.part.part_id,
      version: r.part.version,
      order_no: r.orderNo,
      system_delivery_date: r.systemDeliveryDate,
    })
  }
  return out
})

const effectiveCount = computed(() => effectiveItems.value.length)

// ============================================================
// 行类名 / 标签工具
// ============================================================

function rowClassName({ row }: { row: PreviewRow }): string {
  if (failedRows.value.has(row.part?.part_id ?? '')) return 'row-failed'
  if (row.unmatched) return 'row-unmatched'
  if (row.hasWarnings) return 'row-warnings'
  return ''
}

function matchTagType(t: PreviewRow['matchType']): 'success' | 'warning' | 'danger' | 'info' {
  if (t === 'PART_CODE' || t === 'ASSEMBLY_CODE') return 'success'
  if (t === 'PART_NAME' || t === 'ASSEMBLY_NAME') return 'warning'
  return 'info'
}

function matchTagText(t: PreviewRow['matchType']): string {
  if (t === 'PART_CODE') return '零件编号'
  if (t === 'PART_NAME') return '零件名称'
  if (t === 'ASSEMBLY_CODE') return '装配件编号'
  if (t === 'ASSEMBLY_NAME') return '装配件名称'
  return '未匹配'
}

// ============================================================
// 重置 / 关闭
// ============================================================

function reset(): void {
  docNo.value = ''
  parseErrors.value = []
  parseWarnings.value = []
  matchErrors.value = []
  previewRows.value = []
  failedRows.value = new Map()
  // 清掉 el-upload 内部缓存，否则 :limit=1 时再次上传同一文件不会触发 on-change
  uploadRef.value?.clearFiles?.()
}

function onModelValueChange(open: boolean): void {
  emit('update:modelValue', open)
}

function onCancel(): void {
  emit('update:modelValue', false)
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) reset()
  },
)

// ============================================================
// Excel 上传 + 解析 + 匹配
// ============================================================

async function onFileChange(uploadFile: UploadFile): Promise<void> {
  // 替换文件 → 上一轮的预览 / 错误全清
  previewRows.value = []
  parseErrors.value = []
  parseWarnings.value = []
  matchErrors.value = []
  failedRows.value = new Map()

  const raw: UploadRawFile | undefined = uploadFile.raw
  if (!raw) {
    parseErrors.value = ['未读取到文件内容']
    return
  }

  parsing.value = true
  try {
    const buf = await raw.arrayBuffer()
    const parsed = parsePurchaseOrderExcel(buf)
    docNo.value = parsed.docNo
    parseErrors.value = parsed.errors
    parseWarnings.value = parsed.warnings

    if (parsed.errors.length > 0) {
      // 致命错误：不再请求 match
      previewRows.value = []
      return
    }
    if (parsed.items.length === 0) {
      parseWarnings.value = [...parseWarnings.value, 'Excel 没有可识别的有效明细行']
      previewRows.value = []
      return
    }

    const matchItems: PartBatchOrderInfoMatchItem[] = parsed.items.map((it) => ({
      row_no: it.rowNo,
      line_no: it.lineNo,
      drawing_no: it.drawingNo || null,
      name: it.name || null,
      delivery_date: it.deliveryDate,
      unit_price: it.unitPrice,
      quantity: it.shippableQty,
    }))

    const results = await matchPartsByExcelItems({
      doc_no: parsed.docNo,
      items: matchItems,
    })
    previewRows.value = buildPreviewRows(parsed.items, results)
  } catch (e) {
    parseErrors.value = [(e as Error).message ?? 'Excel 解析或匹配失败']
    previewRows.value = []
  } finally {
    parsing.value = false
  }
}

/** el-upload :limit=1 超限时触发；保留旧文件、丢弃新文件。 */
function onExceed(_files: File[]): void {
  ElMessage.warning('已选择过 Excel，请先取消或重置后再上传新文件')
}

function buildPreviewRows(
  items: PurchaseOrderExcelItem[],
  results: PartBatchOrderInfoMatchResult[],
): PreviewRow[] {
  const resultByRow = new Map<number, PartBatchOrderInfoMatchResult>(
    results.map((r) => [r.row_no, r]),
  )
  return items.map((it) => {
    const r = resultByRow.get(it.rowNo)
    const firstPart: PartMatchInfo | null = r?.parts?.[0] ?? null
    const warnings = r?.warnings ?? []
    return {
      rowNo: it.rowNo,
      lineNo: it.lineNo,
      excelDrawingNo: it.drawingNo,
      excelName: it.name,
      matchType: r?.match_type ?? 'NONE',
      part: firstPart,
      parts: r?.parts ?? [],
      warnings,
      unmatched: firstPart === null,
      hasWarnings: warnings.length > 0,
      orderNo: docNo.value,
      systemDeliveryDate: it.deliveryDate,
      // 默认跳过未匹配；用户可手动勾上「跳过」复选框
      skip: firstPart === null,
    }
  })
}

// ============================================================
// 提交更新
// ============================================================

async function onConfirm(): Promise<void> {
  const items = effectiveItems.value
  if (items.length === 0) {
    ElMessage.warning('没有可更新的零件')
    return
  }

  try {
    await ElMessageBox.confirm(
      `将更新 ${items.length} 个零件的订单号与系统交期，是否继续？`,
      '确认更新',
      { type: 'warning', confirmButtonText: '更新', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }

  submitting.value = true
  try {
    const payloadItems: PartBatchOrderInfoUpdateItem[] = items.map((it) => ({
      part_id: it.part_id,
      version: it.version,
      order_no: it.order_no,
      system_delivery_date: it.system_delivery_date,
    }))
    const result = await batchUpdatePartsOrderInfo({ items: payloadItems })

    failedRows.value = new Map(
      result.failed.map((f) => [f.part_id, `${f.code}: ${f.message}`]),
    )

    if (result.failed.length === 0) {
      ElMessage.success(`已更新 ${result.updated.length} 个零件`)
      emit('success')
      emit('update:modelValue', false)
      return
    }

    const updated = result.updated.length
    const failed = result.failed.length
    if (updated === 0) {
      ElMessage.error(`全部 ${failed} 条更新失败，请检查失败行`)
    } else {
      ElMessage.warning(`更新 ${updated} 条，失败 ${failed} 条；失败行已标红，可修正后重试`)
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '批量更新失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style lang="scss" scoped>
.dlg-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;

  .doc-no {
    display: flex;
    align-items: center;
    gap: 4px;
    .label {
      color: #606266;
      font-size: 13px;
    }
  }
}

.alert-bar {
  margin: 0 0 8px 0;
  &:last-child {
    margin-bottom: 12px;
  }
}

.target-line {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  line-height: 1.4;
}

.assembly-line {
  font-size: 12px;
  margin-top: 2px;
}

.edit-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  .cur {
    color: #909399;
    font-size: 12px;
  }
}

.small {
  font-size: 12px;
}

.dlg-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.preview-stats {
  color: #606266;
  font-size: 13px;
  margin-right: 12px;
  flex: 1;
  text-align: left;
}

.mono {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
}

.muted {
  color: #909399;
}

:deep(.row-unmatched) {
  background: #fef0f0 !important;
}

:deep(.row-warnings) {
  background: #fdf6ec !important;
}

:deep(.row-failed) {
  background: #fde2e2 !important;
}
</style>
