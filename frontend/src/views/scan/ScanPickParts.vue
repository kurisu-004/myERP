<!--
  ScanPickParts.vue

  /scan/pick —— 扫码台 PICK_UP 新流程
  1. 拉取 worker.work_type_id 映射下、当前货架上的零件列表
  2. 工人点选一个零件 → 进入「等待扫码」状态
  3. 扫码枪输入 serial_no；前端校验必须等于选中零件.serial_no；不等则拒绝
  4. 通过则调 POST /parts/pick-up；成功后自动回到列表（可选再选下一件）
-->

<template>
  <div class="scan-pick">
    <!-- 顶栏 -->
    <div class="topbar">
      <div class="topbar-left">
        <el-icon :size="22" color="#fff"><Avatar /></el-icon>
        <span class="title">报工台</span>
        <el-divider direction="vertical" class="divider" />
        <span class="worker-name">{{ worker?.name ?? '—' }}</span>
        <el-tag size="default" type="info" effect="dark" class="badge-tag">
          {{ worker?.badge_code ?? '' }}
        </el-tag>
        <el-divider direction="vertical" class="divider" />
        <el-tag type="primary" effect="dark">取 件</el-tag>
      </div>
      <div class="topbar-right">
        <HeldPartsBadge v-if="worker?.id" :worker-id="String(worker.id)" :auto-open-on-change="true" />
        <el-button type="info" plain @click="backToAction">
          <el-icon><Back /></el-icon>
          <span>返回操作选择</span>
        </el-button>
        <el-button type="warning" plain @click="backToBadge">
          <el-icon><Refresh /></el-icon>
          <span>重新扫工牌</span>
        </el-button>
      </div>
    </div>

    <div ref="contentRef" class="content">
      <div v-if="loadingList" class="loading-block">
        <el-icon :size="32" class="is-loading"><Loading /></el-icon>
        <p>加载可领件列表…</p>
      </div>

      <div v-else-if="!worker?.work_type_id" class="empty-block">
        <el-icon :size="60" color="#e6a23c"><Warning /></el-icon>
        <h3>未分配工种</h3>
        <p>请联系管理员在「权限管理 → 工人一览」中为本工牌指派工种。</p>
        <el-button type="primary" @click="backToAction">返回</el-button>
      </div>

      <div v-else-if="parts.length === 0" class="empty-block">
        <el-icon :size="60" color="#c0c4cc"><Box /></el-icon>
        <h3>当前工种无可领件</h3>
        <p>该工种在所有生产货架上没有匹配「下一道工序」的零件。</p>
        <el-button @click="refresh" type="primary">刷新</el-button>
        <el-button @click="backToAction">返回</el-button>
      </div>

      <div v-else>
        <div class="parts-header">
          <el-icon :size="24"><Box /></el-icon>
          <span class="parts-header-text">可领件列表</span>
          <el-tag type="info" effect="plain" size="large" class="count-tag">
            共 {{ parts.length }} 件
          </el-tag>
          <el-button :icon="Refresh" circle size="small" @click="refresh" />
        </div>

        <div v-if="selectedPart" class="confirm-bar">
          <el-icon :size="20" color="#409eff" class="is-loading"><Aim /></el-icon>
          <span class="confirm-text">
            已选 <strong>{{ selectedPart.serial_no || selectedPart.drawing_no }}</strong>
            <el-tag v-if="selectedPart.batch_no" type="info" size="small" effect="plain">
              批次{{ selectedPart.batch_no }}
            </el-tag>
            · {{ selectedPart.name }}
            · 当前所在 {{ selectedPart.shelf_code || '?' }}
            · 数量 {{ selectedPart.quantity }}
            · 请扫描该零件的序列号条码确认
          </span>
          <el-button size="small" @click="cancelSelect">取消选择</el-button>
        </div>

        <div class="parts-list">
          <el-card
            v-for="p in sortedParts"
            :key="p.batch_id || p.id"
            :data-batch-id="String(p.batch_id || p.id)"
            shadow="hover"
            class="part-row"
            :class="{
              'is-selected': sameBatch(selectedPart, p),
              'is-urgent': p.is_urgent,
              'is-inspection-failed': !!p.last_inspection_fail_note,
            }"
            @click="onSelect(p)"
          >
            <div class="part-row-main">
              <!-- 右上角预览按钮（@click.stop 阻止冒泡触发选中） -->
              <el-button
                text
                size="small"
                type="info"
                class="preview-btn"
                :loading="previewLoading && previewPart?.id === p.id"
                @click.stop="onPreview(p)"
              >
                <el-icon><View /></el-icon>
                <span>预览</span>
              </el-button>

              <!-- 1) 序列号 + 交期 高优行 -->
              <div class="part-line-top">
                <span class="serial-no">{{ p.serial_no || p.drawing_no }}</span>
                <el-tag
                  v-if="p.batch_no"
                  type="info"
                  size="small"
                  effect="plain"
                >批次{{ p.batch_no }}</el-tag>
                <el-tag
                  v-if="p.is_urgent"
                  type="danger"
                  size="small"
                  effect="dark"
                  class="urgent-pulse"
                >加急</el-tag>
                <el-tag
                  v-if="p.last_inspection_fail_note"
                  type="warning"
                  size="small"
                  effect="dark"
                  class="fail-pulse"
                >品检打回</el-tag>
                <DeliveryDateChip
                  :planned-delivery-date="p.planned_delivery_date"
                  :system-delivery-date="p.system_delivery_date"
                />
              </div>

              <!-- 2) 名称 + 客户 -->
              <div class="part-line-name">
                <span class="part-name">{{ p.name }}</span>
                <span v-if="p.customer_path" class="customer">· {{ p.customer_path }}</span>
              </div>

              <!-- 2.5) 2026-07-21：品检打回备注（仅 last_inspection_fail_note 非空时显示） -->
              <div v-if="p.last_inspection_fail_note" class="inspection-fail-note">
                <el-icon class="fail-note-icon"><Warning /></el-icon>
                <span class="fail-note-label">品检备注</span>
                <span class="fail-note-text">{{ p.last_inspection_fail_note }}</span>
              </div>

              <!-- 3) 数量 + 货架码 -->
              <div class="part-line-bottom">
                <span class="qty">× {{ p.quantity }}</span>
                <span class="shelf-code-wrap">
                  <el-icon><Box /></el-icon>
                  <span>{{ p.shelf_code || '未上架' }}</span>
                </span>
              </div>
            </div>
          </el-card>
        </div>
      </div>
    </div>

    <ScrollFabPair :target="contentRef" />

    <!-- 同条码多批次选择弹窗 -->
    <BatchPickerDialog
      v-if="showBatchPicker"
      v-model="showBatchPicker"
      :code="batchPickerCode"
      :rows="batchPickerRows"
      @pick="onBatchPicked"
    />

    <!-- 数量选择弹窗 -->
    <QuantityDialog
      v-if="showQtyDialog"
      v-model="showQtyDialog"
      :max="selectedPart?.quantity ?? 1"
      :serial-no="selectedPart?.serial_no || selectedPart?.drawing_no || null"
      :part-name="selectedPart?.name || null"
      action-label="领取"
      @confirm="onQtyConfirm"
      @cancel="showQtyDialog = false"
    />

    <!-- 图纸 / 图片 全屏预览 -->
    <el-dialog
      v-model="showPreview"
      :title="previewTitle"
      fullscreen
      :close-on-click-modal="false"
      destroy-on-close
      @closed="onPreviewClosed"
    >
      <div v-if="previewLoading" class="preview-loading">
        <el-icon :size="32" class="is-loading"><Loading /></el-icon>
        <span>加载图纸中…</span>
      </div>

      <PdfViewer
        v-else-if="previewFile && isPdf(previewFile.file_type)"
        :url="previewBlobUrl"


      />

      <div
        v-else-if="previewFile && isImage(previewFile.file_type)"
        class="image-preview-wrap"
      >
        <el-image
          v-if="!isHeic(previewFile.file_type)"
          :src="previewBlobUrl"
          :preview-src-list="[previewBlobUrl]"
          :initial-index="0"
          fit="contain"
          style="max-width: 100%; max-height: calc(100vh - 80px);"
        />
        <div v-else class="non-pdf-preview">
          <el-icon :size="48" color="#67c23a"><Picture /></el-icon>
          <p class="non-pdf-name">{{ previewFile.original_filename }}</p>
          <p class="non-pdf-hint">HEIC 格式浏览器不直接支持预览，请下载后查看。</p>
          <el-button type="primary" @click="downloadPreview">
            <el-icon><Download /></el-icon><span>下载文件</span>
          </el-button>
        </div>
      </div>

      <div v-else class="non-pdf-preview">
        <el-icon :size="48" color="#909399"><Files /></el-icon>
        <p class="non-pdf-name">{{ previewFile?.original_filename || '该零件暂无图纸' }}</p>
        <p class="non-pdf-hint">
          {{ previewFile ? `${previewFile.file_type} 文件不支持浏览器内嵌预览，请下载后查看。` : '请上传图纸后再预览。' }}
        </p>
        <el-button v-if="previewFile" type="primary" @click="downloadPreview">
          <el-icon><Download /></el-icon><span>下载文件</span>
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeMount, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Aim,
  Avatar,
  Back,
  Box,
  Download,
  Files,
  Loading,
  Picture,
  Refresh,
  View,
  Warning,
} from '@element-plus/icons-vue'
import { api } from '@/api/http'
import PdfViewer from '@/components/PdfViewer.vue'
import { getDownloadUrl, listPartFiles } from '@/api/assembly'
import type { PartFileItem } from '@/types/part_file'
import { useScanSession } from '@/composables/useScanSession'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useActiveShelfSelection } from '@/composables/useActiveShelfSelection'
import { useScanBus } from '@/composables/useScanBus'
import { useScanPartsSort } from '@/composables/useScanPartsSort'
import HeldPartsBadge from '@/views/scan/components/HeldPartsBadge.vue'
import ScrollFabPair from '@/views/scan/components/ScrollFabPair.vue'
import QuantityDialog from '@/views/scan/components/QuantityDialog.vue'
import { listPartsByWorkTypeAllShelves, pickUpPart, type PartItem } from '@/api/parts'
import { findAllByCode, findPartBySerialAndPrompt } from '@/utils/scanHelpers'
import BatchPickerDialog from '@/views/scan/components/BatchPickerDialog.vue'
import DeliveryDateChip from '@/views/scan/components/DeliveryDateChip.vue'

const router = useRouter()
const { worker, requireWorker, reset: resetScanSession } = useScanSession()
const { onScan } = useBarcodeScanner()
const { emitHeldChanged } = useScanBus()
// 2026-07-13：跨架列表展示用 listPartsByWorkTypeAllShelves（后端按 user.shelf_ids 收口）；
// shelfId 提交兜底用 useActiveShelfSelection.selectedShelfId（多架场景工人已在 action picker
// 顶部选好当前作业架；单架时直接 = 唯一架 id；wildcard 时为 null）。
const shelfSel = useActiveShelfSelection()

const shelfId = ref<string>('')
const parts = ref<PartItem[]>([])
// 「系统交期」硬优先级 + 原 is_urgent / planned_delivery_date 排序；详见 composable 注释
const sortedParts = useScanPartsSort(parts)

const contentRef = ref<HTMLElement | null>(null)
const loadingList = ref(false)
const selectedPart = ref<PartItem | null>(null)
const submitting = ref(false)
const showQtyDialog = ref(false)

// --- 多批次扫码命中弹窗状态 ---
const showBatchPicker = ref(false)
const batchPickerCode = ref('')
const batchPickerRows = ref<PartItem[]>([])

// --- 预览状态 ---
const showPreview = ref(false)
const previewLoading = ref(false)
const previewPart = ref<PartItem | null>(null)
const previewFile = ref<PartFileItem | null>(null)
const previewBlobUrl = ref<string>('')
// 防竞态：每次开预览自增，老请求响应直接丢弃
let previewToken = 0

const previewTitle = computed<string>(
  () => `预览 — ${previewPart.value?.serial_no || previewPart.value?.drawing_no || ''}`,
)

// --- 类型判定（与 FileListCard.vue 295-302 同步） ---
function isPdf(t: string): boolean { return t.toUpperCase() === 'PDF' }
const IMAGE_TYPES = new Set(['PNG', 'JPG', 'JPEG', 'GIF', 'BMP', 'TIF', 'TIFF', 'WEBP'])
function isImage(t: string): boolean { return IMAGE_TYPES.has(t.toUpperCase()) }
function isHeic(t: string): boolean { return t.toUpperCase() === 'HEIC' }

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  // 多架 SHELF_ACCOUNT：worker 已在 ScanActionPicker 顶部选好当前作业架；
  // 单架时直接 = shelfSel.selectedShelfId（唯一架）；wildcard 时为 null（兜底到 current_holder_id）
  shelfId.value = shelfSel.selectedShelfId.value ?? ''
  await refresh()
})

async function refresh(): Promise<void> {
  if (!worker.value?.work_type_id) return
  loadingList.value = true
  try {
    // 跨架列表（后端 api/v1/part.py::list_pickable_parts_by_work_type_all_shelves
    // 已按 user.shelf_ids 收口；scoped SHELF_ACCOUNT 只看到自己绑定的架上的件）
    parts.value = await listPartsByWorkTypeAllShelves(worker.value.work_type_id)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载列表失败')
  } finally {
    loadingList.value = false
  }
}

const selectedQty = ref<number | undefined>(undefined)

/** 2026-07-29 批次化：行=批次，选中比较按 batch_id（无批次信息退回 part id） */
function sameBatch(a: PartItem | null, b: PartItem): boolean {
  if (!a) return false
  if (a.batch_id && b.batch_id) return a.batch_id === b.batch_id
  return a.id === b.id
}

function onSelect(p: PartItem): void {
  if (submitting.value) return
  // 取消选中（已选同一件 → 反选）
  if (sameBatch(selectedPart.value, p)) {
    cancelSelect()
    return
  }
  selectedPart.value = p
  selectedQty.value = p.quantity
  ElMessage.info(`已选中 ${p.serial_no || p.drawing_no}，请扫码确认`)
}

function cancelSelect(): void {
  selectedPart.value = null
  selectedQty.value = undefined
}

// --- 预览 ---
async function onPreview(p: PartItem): Promise<void> {
  previewPart.value = p
  showPreview.value = true
  previewLoading.value = true
  const myToken = ++previewToken
  try {
    // 1. 取该零件的 DRAWING 文件列表
    const files = await listPartFiles(String(p.id), 'DRAWING')
    if (myToken !== previewToken) return
    if (!files.length) {
      ElMessage.warning('暂无图纸')
      showPreview.value = false
      return
    }
    const f = files[0]
    previewFile.value = f
    // 2. PDF / 图片需要 blob URL；HEIC / STEP / DWG 等直接走下载按钮
    if (isPdf(f.file_type) || isImage(f.file_type)) {
      const resp = await api.get(`/files/${f.id}/content`, { responseType: 'blob' })
      if (myToken !== previewToken) return
      if (previewBlobUrl.value) URL.revokeObjectURL(previewBlobUrl.value)
      previewBlobUrl.value = URL.createObjectURL(resp.data)
    }
  } catch (e) {
    if (myToken !== previewToken) return
    ElMessage.error((e as Error).message ?? '加载图纸失败')
    showPreview.value = false
  } finally {
    if (myToken === previewToken) previewLoading.value = false
  }
}

function onPreviewClosed(): void {
  if (previewBlobUrl.value) {
    URL.revokeObjectURL(previewBlobUrl.value)
    previewBlobUrl.value = ''
  }
  previewPart.value = null
  previewFile.value = null
}

async function downloadPreview(): Promise<void> {
  if (!previewFile.value) return
  try {
    const url = await getDownloadUrl(previewFile.value.id)
    const a = document.createElement('a')
    a.href = url
    a.target = '_blank'
    a.rel = 'noopener'
    a.download = ''
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下载失败')
  }
}

// --- 扫码：扫描直接选中 + 滚动居中 + 打开下一弹窗；不在列表则提示当前位置 ---

/** 选中后等一拍再滚动；元素不在容器内则静默返回 */
async function scrollCardIntoView(batchKey: string): Promise<void> {
  await nextTick()
  const root = contentRef.value
  if (!root) return
  const el = root.querySelector<HTMLElement>(
    `.part-row[data-batch-id="${CSS.escape(batchKey)}"]`,
  )
  if (!el || !root.contains(el)) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/** PICK tail：选中 + 滚动 + 校验 shelf_id + 开数量弹窗 */
async function applyScanSelection(p: PartItem): Promise<void> {
  selectedPart.value = p
  selectedQty.value = p.quantity
  const key = String(p.batch_id || p.id)
  await scrollCardIntoView(key)
  if (!worker.value) return
  // 多架/单架/wildcard 三态统一：选中件的实际 current_holder_id（来自后端收口后的
  // 列表）作 shelf_id 主路径；兜底用 shelfSel.selectedShelfId（单架时 = 唯一架
  // id；wildcard 时为 null）。
  const useShelfId = p.current_holder_id || shelfId.value
  if (!useShelfId) {
    ElMessage.error('未找到零件所在货架信息')
    return
  }
  showQtyDialog.value = true
}

async function onScanToSelect(rawCode: string): Promise<void> {
  const code = rawCode.trim()
  if (!code) return
  if (submitting.value || showQtyDialog.value || showBatchPicker.value || showPreview.value) return
  const matches = findAllByCode(parts.value, code)
  if (matches.length === 1) {
    await applyScanSelection(matches[0])
  } else if (matches.length > 1) {
    batchPickerCode.value = code
    batchPickerRows.value = matches
    showBatchPicker.value = true
  } else {
    await findPartBySerialAndPrompt(code)
  }
}

function onBatchPicked(p: PartItem): void {
  showBatchPicker.value = false
  void applyScanSelection(p)
}

const unsub = onScan((code) => { void onScanToSelect(code) })

onBeforeUnmount(() => {
  unsub()
  if (previewBlobUrl.value) URL.revokeObjectURL(previewBlobUrl.value)
})

async function onQtyConfirm(qty: number): Promise<void> {
  showQtyDialog.value = false
  if (!selectedPart.value || !worker.value) return
  const code = selectedPart.value.serial_no || selectedPart.value.drawing_no || ''
  const useShelfId = selectedPart.value.current_holder_id || shelfId.value
  if (!useShelfId) {
    ElMessage.error('未找到零件所在货架信息')
    return
  }
  selectedQty.value = qty
  submitting.value = true
  try {
    await pickUpPart({
      serial_no: code,
      shelf_id: useShelfId,
      badge_code: worker.value.badge_code,
      batch_id: selectedPart.value.batch_id ?? null,
      quantity: qty,
    })
    ElMessage.success(`已领取: ${code} × ${qty}`)
    selectedPart.value = null
    await refresh()
    emitHeldChanged()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '领取失败')
  } finally {
    submitting.value = false
  }
}

function backToAction(): void {
  selectedPart.value = null
  void router.replace('/scan/action')
}

function backToBadge(): void {
  selectedPart.value = null
  resetScanSession()
  void router.replace('/scan/badge')
}
</script>

<style lang="scss" scoped>
.scan-pick {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(90deg, #142d54 0%, #1e4d8b 100%);
  color: #fff;
  padding: 12px 24px;
  height: 60px;
  flex-shrink: 0;
}
.topbar-left { display: flex; align-items: center; gap: 12px; font-size: 16px; }
.topbar-right { display: flex; gap: 8px; }
.title { font-size: 18px; font-weight: 700; letter-spacing: 2px; }
.divider { background: rgba(255,255,255,.3); height: 20px; }
.worker-name { font-size: 18px; font-weight: 600; }
.badge-tag { font-family: 'SF Mono', Menlo, Consolas, monospace; }

.content {
  flex: 1;
  overflow: auto;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
}

.loading-block, .empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  gap: 12px;
  color: #606266;
  text-align: center;
  h3 { font-size: 20px; margin: 0; color: #303133; }
  p { color: #909399; max-width: 480px; }
}

.parts-header {
  display: flex; align-items: center; gap: 12px; margin-bottom: 16px; color: #303133;
}
.parts-header-text { font-size: 20px; font-weight: 600; }
.count-tag { font-size: 16px; padding: 6px 14px; }

.confirm-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 8px;
  margin-bottom: 16px;
}
.confirm-text { flex: 1; color: #303133; }
.confirm-text strong {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: #409eff; font-size: 18px; margin: 0 4px;
}

.parts-list { display: flex; flex-direction: column; gap: 10px; }

.part-row {
  display: flex !important;
  align-items: stretch;
  padding: 14px 18px !important;
  border: 1px solid #e4e7ed;
  border-left: 4px solid #409eff;
  border-radius: 8px;
  cursor: pointer;
  position: relative;
  background: #fff;
  transition: border-color .15s, background .15s, box-shadow .15s;
}
.part-row:hover {
  box-shadow: 0 2px 12px rgba(64, 158, 255, .08);
}

/* 非加急选中 → 加深绿底 */
.part-row.is-selected {
  background: #e1f3d8;
  border-color: #67c23a;
}
/* 加急未选中 → 原红底 */
.part-row.is-urgent {
  background: #fef0f0;
  border-color: #f56c6c;
  border-left-color: #f56c6c;
}
/* 加急选中 → 保持红底，绿色边框 + inset 阴影表示选中 */
.part-row.is-urgent.is-selected {
  background: #fef0f0;
  border-color: #67c23a;
  box-shadow: 0 0 0 2px #67c23a inset;
}

/* 2026-07-21：品检打回件 —— 橙色边框 + 浅橙背景；与加急红、加急选中绿视觉区分 */
.part-row.is-inspection-failed {
  background: #fdf6ec;
  border-color: #e6a23c;
  border-left-color: #e6a23c;
}
.part-row.is-inspection-failed.is-selected {
  background: #fdf6ec;
  border-color: #67c23a;
  box-shadow: 0 0 0 2px #67c23a inset;
}
/* 加急 + 品检打回同时命中 → 加急样式优先（红底），但保留橙色左边框作为"打回"标识 */
.part-row.is-urgent.is-inspection-failed {
  background: #fef0f0;
  border-color: #f56c6c;
  border-left: 4px solid #e6a23c;
}

.part-row-main { display: flex; flex-direction: column; gap: 6px; width: 100%; min-width: 0; }
.preview-btn { position: absolute !important; top: 8px; right: 10px; z-index: 1; }

.part-line-top { display: flex; align-items: center; gap: 12px; padding-right: 64px; flex-wrap: wrap; }
.serial-no {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 22px; font-weight: 700; color: #303133; letter-spacing: 0.5px;
}

.part-line-name { display: flex; align-items: center; gap: 10px; font-size: 15px; color: #303133; }
.part-name { font-weight: 500; color: #303133; }
.customer { color: #909399; font-size: 13px; }

.part-line-bottom { display: flex; align-items: center; gap: 16px; font-size: 14px; color: #606266; flex-wrap: wrap; }
.qty { color: #409eff; font-weight: 700; font-size: 15px; }
.shelf-code-wrap { display: inline-flex; align-items: center; gap: 4px; color: #909399; }

.is-loading { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

@keyframes urgentPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.urgent-pulse {
  animation: urgentPulse 1.2s ease-in-out infinite;
}

/* 2026-07-21：品检打回备注卡片 —— 浅橙底 + 深橙文字（与「快要到期」chip 同色系） */
.inspection-fail-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 4px;
  padding: 6px 10px;
  background: rgba(230, 162, 60, 0.10);
  border: 1px solid #faecd8;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.5;
  color: #8a5a1f;
  word-break: break-all;
}
.inspection-fail-note .fail-note-icon {
  color: #e6a23c;
  font-size: 14px;
  flex-shrink: 0;
  margin-top: 1px;
}
.inspection-fail-note .fail-note-label {
  font-weight: 600;
  flex-shrink: 0;
}
.inspection-fail-note .fail-note-text {
  flex: 1;
  min-width: 0;
}
@keyframes failPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
.fail-pulse {
  animation: failPulse 1.6s ease-in-out infinite;
}

.preview-loading {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  min-height: 60vh; color: #606266; font-size: 16px;
}
.image-preview-wrap {
  display: flex; align-items: center; justify-content: center;
  min-height: calc(100vh - 80px); padding: 24px; background: #1e1e1e;
}
.non-pdf-preview {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  padding: 80px 32px;
}
.non-pdf-name {
  margin: 0; font-size: 16px; font-weight: 600; color: #303133;
}
.non-pdf-hint {
  margin: 0; color: #606266; font-size: 14px;
}
</style>