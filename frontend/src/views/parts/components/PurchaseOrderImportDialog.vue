<!--
  PurchaseOrderImportDialog.vue

  /parts? 采购订单 Excel 导入对话框。
  流程：上传 .xlsx → parsePurchaseOrderExcel → matchPartsByExcelItems
       → 预览（嵌套展开行 + 多候选手动勾选 + 改订单号 / 交期）→ batchUpdatePartsOrderInfo

  设计要点：
  - 解析纯函数 + 后端 match API：行 = 一个 Excel 物料行；展示匹配零件 + 警告。
  - 主行 = 一个 Excel 行；展开行 = 该 Excel 行的多个候选零件，每个候选独立勾选 + 编辑字段。
  - 默认选中「order_no 与 system_delivery_date 都为空」的候选（isEmptyTarget）。
  - 未匹配行无候选（展开箭头可见但展开内容为空）；主行红色背景。
  - 后端 OCC（version）由后端从 match 返回，前端原样回传。
  - 批量更新失败时，保留对话框，用 failedRows 标红失败候选行 + 主行（任一候选失败 → 主行变红）。
  - 同一 part_id 跨多个 group 重复时，effectiveItems 去重（防御性）。
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

    <!-- 预览表（嵌套展开行） -->
    <el-table
      v-if="previewGroups.length > 0"
      :data="previewGroups"
      row-key="rowNo"
      :row-class-name="rowClassName"
      :preserve-expanded-content="true"
      :max-height="500"
      size="small"
      border
      stripe
    >
      <!-- 展开列：嵌套候选子表 -->
      <el-table-column type="expand">
        <template #default="scope">
          <div
            v-if="(scope.row as PreviewGroup).candidates.length > 0"
            class="candidate-panel"
          >
            <el-table
              :data="(scope.row as PreviewGroup).candidates"
              :row-class-name="candidateRowClassName"
              class="candidate-table"
              size="small"
              border
              stripe
            >
              <!-- 选择 checkbox 列 -->
              <el-table-column label="选择" width="64" align="center">
                <template #default="{ row }">
                  <el-checkbox
                    v-model="(row as CandidateRow).selected"
                    :disabled="failedRows.has((row as CandidateRow).part.part_id)"
                  />
                </template>
              </el-table-column>

              <!-- 候选零件（图号 + 名称 + 装配件副标题） -->
              <el-table-column label="候选零件" min-width="250">
                <template #default="{ row }">
                  <div class="target-line">
                    <span class="mono">{{ (row as CandidateRow).part.drawing_no || '—' }}</span>
                    <span>{{ (row as CandidateRow).part.name }}</span>
                  </div>
                  <div
                    v-if="(row as CandidateRow).part.assembly_name"
                    class="assembly-line muted"
                  >
                    所属装配件：{{ (row as CandidateRow).part.assembly_name }}
                  </div>
                </template>
              </el-table-column>

              <!-- 现订单号 → 新订单号 -->
              <el-table-column label="现订单号 → 新订单号" min-width="220">
                <template #default="{ row }">
                  <div class="edit-stack">
                    <span class="cur mono">{{
                      (row as CandidateRow).part.order_no || '—'
                    }}</span>
                    <el-input
                      v-model="(row as CandidateRow).orderNo"
                      size="small"
                      :disabled="failedRows.has((row as CandidateRow).part.part_id)"
                      placeholder="新订单号"
                    />
                  </div>
                </template>
              </el-table-column>

              <!-- 现交期 → 新交期 -->
              <el-table-column label="现交期 → 新交期" min-width="240">
                <template #default="{ row }">
                  <div class="edit-stack">
                    <span class="cur mono">{{
                      (row as CandidateRow).part.system_delivery_date || '—'
                    }}</span>
                    <el-date-picker
                      v-model="(row as CandidateRow).systemDeliveryDate"
                      type="date"
                      value-format="YYYY-MM-DD"
                      :disabled="failedRows.has((row as CandidateRow).part.part_id)"
                      size="small"
                      style="width: 160px"
                      placeholder="新系统交期"
                    />
                  </div>
                </template>
              </el-table-column>

              <!-- 状态 -->
              <el-table-column label="状态" min-width="150">
                <template #default="{ row }">
                  <template v-if="failedRows.has((row as CandidateRow).part.part_id)">
                    <el-tag type="danger" effect="light" size="small">更新失败</el-tag>
                    <span class="failure-text">
                      {{ failedRows.get((row as CandidateRow).part.part_id) }}
                    </span>
                  </template>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>

      <!-- 主行列 -->
      <el-table-column label="候选数" width="100" align="center">
        <template #default="{ row }">
          <el-tag
            v-if="(row as PreviewGroup).candidates.length > 0"
            type="success"
            effect="plain"
            size="small"
          >
            {{ (row as PreviewGroup).candidates.length }} 候选
          </el-tag>
          <el-tag v-else type="danger" effect="light" size="small">未匹配</el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="rowNo" label="Excel 行号" width="92" align="center" />

      <el-table-column label="物料代码" min-width="170" align="center">
        <template #default="{ row }">
          <span class="mono">{{ (row as PreviewGroup).excelDrawingNo || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="描述" min-width="170" align="center">
        <template #default="{ row }">
          <span>{{ (row as PreviewGroup).excelName || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="匹配方式" width="120" align="center">
        <template #default="{ row }">
          <el-tag
            :type="matchTagType((row as PreviewGroup).matchType)"
            effect="light"
            size="small"
          >
            {{ matchTagText((row as PreviewGroup).matchType) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="警告" width="90" align="center">
        <template #default="{ row }">
          <el-tooltip
            v-if="(row as PreviewGroup).warnings.length > 0"
            :content="(row as PreviewGroup).warnings.join('；')"
            placement="top"
            :show-after="200"
          >
            <el-tag type="warning" effect="plain" size="small">
              {{ (row as PreviewGroup).warnings.length }} 条
            </el-tag>
          </el-tooltip>
          <span v-else class="muted">—</span>
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
          共 {{ groupsCount }} 行，匹配 {{ matchedGroupsCount }} 个 Excel 行（含
          {{ candidatesTotalCount }} 个候选），未匹配 {{ unmatchedGroupsCount }} 行，将更新
          {{ effectiveCount }} 个
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
// 行模型（v0.3.3 follow-up：嵌套展开 + 多候选）
// ============================================================

/**
 * 主行 = 一个 Excel 物料行；展开行 = 该 Excel 行的多个候选零件。
 * `candidates` 长度 0 / 1 / N；0 即未匹配（主行红底 + 展开箭头但内容空）。
 */
interface PreviewGroup {
  rowNo: number
  lineNo: string
  excelDrawingNo: string
  excelName: string
  matchType: 'PART_CODE' | 'PART_NAME' | 'ASSEMBLY_CODE' | 'ASSEMBLY_NAME' | 'NONE'
  warnings: string[]
  candidates: CandidateRow[]
}

interface CandidateRow {
  part: PartMatchInfo
  /** 默认 = isEmptyTarget(part)，用户可改 */
  selected: boolean
  /** 默认 = 当前 docNo */
  orderNo: string
  /** 默认 = Excel 行的 deliveryDate */
  systemDeliveryDate: string | null
}

// ============================================================
// 状态
// ============================================================

const uploadRef = ref<{ clearFiles?: () => void } | null>(null)
const docNo = ref('')
const parseErrors = ref<string[]>([])
const parseWarnings = ref<string[]>([])
const matchErrors = ref<string[]>([])
const previewGroups = ref<PreviewGroup[]>([])
const parsing = ref(false)
const submitting = ref(false)
/** part_id → 失败原因。用于标红失败候选行 + 失败主行（任一候选失败 → 主行变红）。 */
const failedRows = ref<Map<string, string>>(new Map())

// ============================================================
// Computed
// ============================================================

const groupsCount = computed(() => previewGroups.value.length)
const matchedGroupsCount = computed(
  () => previewGroups.value.filter((g) => g.candidates.length > 0).length,
)
const unmatchedGroupsCount = computed(
  () => previewGroups.value.filter((g) => g.candidates.length === 0).length,
)
const candidatesTotalCount = computed(() =>
  previewGroups.value.reduce((s, g) => s + g.candidates.length, 0),
)

interface EffectiveItem {
  part_id: string
  version: number
  order_no: string
  system_delivery_date: string | null
}

/**
 * 把所有 group 的 selected + 非失败的候选展平；防御性去重（同一 part_id
 * 不可能跨 group 出现，但保留 seen 集合以防万一）。
 */
const effectiveItems = computed<EffectiveItem[]>(() => {
  const out: EffectiveItem[] = []
  const seen = new Set<string>()
  for (const g of previewGroups.value) {
    for (const c of g.candidates) {
      if (!c.selected) continue
      if (failedRows.value.has(c.part.part_id)) continue
      if (seen.has(c.part.part_id)) continue
      seen.add(c.part.part_id)
      out.push({
        part_id: c.part.part_id,
        version: c.part.version,
        order_no: c.orderNo,
        system_delivery_date: c.systemDeliveryDate,
      })
    }
  }
  return out
})

const effectiveCount = computed(() => effectiveItems.value.length)

// ============================================================
// 默认选择 helper
// ============================================================

/** 当候选零件的 order_no 和 system_delivery_date 都为空时默认勾选（"待填"零件）。 */
function isEmptyTarget(p: PartMatchInfo): boolean {
  const orderEmpty = !p.order_no || p.order_no.trim() === ''
  const dateEmpty = p.system_delivery_date == null
  return orderEmpty && dateEmpty
}

// ============================================================
// 行类名 / 标签工具
// ============================================================

function rowClassName({ row }: { row: PreviewGroup }): string {
  // 任一候选失败 → 主行变红
  if (row.candidates.some((c) => failedRows.value.has(c.part.part_id))) {
    return 'row-failed'
  }
  if (row.candidates.length === 0) return 'row-unmatched'
  if (row.warnings.length > 0) return 'row-warnings'
  return ''
}

function candidateRowClassName({ row }: { row: CandidateRow }): string {
  return failedRows.value.has(row.part.part_id) ? 'candidate-failed' : ''
}

function matchTagType(
  t: PreviewGroup['matchType'],
): 'success' | 'warning' | 'danger' | 'info' {
  if (t === 'PART_CODE' || t === 'ASSEMBLY_CODE') return 'success'
  if (t === 'PART_NAME' || t === 'ASSEMBLY_NAME') return 'warning'
  return 'info'
}

function matchTagText(t: PreviewGroup['matchType']): string {
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
  previewGroups.value = []
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
  previewGroups.value = []
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
      previewGroups.value = []
      return
    }
    if (parsed.items.length === 0) {
      parseWarnings.value = [...parseWarnings.value, 'Excel 没有可识别的有效明细行']
      previewGroups.value = []
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
    previewGroups.value = buildPreviewGroups(parsed.items, results)
  } catch (e) {
    parseErrors.value = [(e as Error).message ?? 'Excel 解析或匹配失败']
    previewGroups.value = []
  } finally {
    parsing.value = false
  }
}

/** el-upload :limit=1 超限时触发；保留旧文件、丢弃新文件。 */
function onExceed(_files: File[]): void {
  ElMessage.warning('已选择过 Excel，请先取消或重置后再上传新文件')
}

/**
 * 把 Excel 行 + 后端 match 结果合并成嵌套展开行的 PreviewGroup。
 * 每个 part → 一个 CandidateRow；默认 selected = isEmptyTarget(part)。
 */
function buildPreviewGroups(
  items: PurchaseOrderExcelItem[],
  results: PartBatchOrderInfoMatchResult[],
): PreviewGroup[] {
  const resultByRow = new Map<number, PartBatchOrderInfoMatchResult>(
    results.map((r) => [r.row_no, r]),
  )
  return items.map((it) => {
    const r = resultByRow.get(it.rowNo)
    const parts = r?.parts ?? []
    return {
      rowNo: it.rowNo,
      lineNo: it.lineNo,
      excelDrawingNo: it.drawingNo,
      excelName: it.name,
      matchType: r?.match_type ?? 'NONE',
      warnings: r?.warnings ?? [],
      candidates: parts.map((part) => ({
        part,
        selected: isEmptyTarget(part),
        orderNo: docNo.value,
        systemDeliveryDate: it.deliveryDate,
      })),
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

// v0.3.3 follow-up：嵌套展开行的子表样式
.candidate-panel {
  padding: 10px 28px 12px 48px;
  background: #fafafa;
}
.candidate-table {
  width: 100%;
}
.candidate-table :deep(.el-table__header-wrapper th) {
  background: #f5f7fa;
}
.candidate-failed {
  background: #fde2e2 !important;
}
.failure-text {
  display: block;
  margin-top: 3px;
  color: #f56c6c;
  font-size: 12px;
  line-height: 1.35;
}
</style>