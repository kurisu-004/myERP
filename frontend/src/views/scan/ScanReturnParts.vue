<!--
  ScanReturnParts.vue

  /scan/return —— 扫码台 RETURN 新流程（2026-07-10 PR-E）
  1. onBeforeMount 调 GET /parts/by-worker/{worker_id} 列出当前 worker 持有件
  2. 工人点选一件 → 弹「下一道工序」picker（el-radio-group）
  3. 选完工序 → 弹 ShelfPickerDialog（共享 HMI 卡片网格）
  4. 提交 POST /parts/scan (event_type=RETURNED, shelf_id, next_process_id, badge_code)
  5. 成功后自动 refresh（该件从列表消失）

  与 ScanPickParts.vue 范式对齐：
  - 选件 → 选工序 → 选架 → 提交
  - 不需要扫码确认（点选即确认；旧流程「扫一批条码」已替换不保留）
-->

<template>
  <div class="scan-return">
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
        <el-tag type="warning" effect="dark">放 回</el-tag>
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
      <!-- 加载 -->
      <div v-if="loadingList" class="loading-block">
        <el-icon :size="32" class="is-loading"><Loading /></el-icon>
        <p>加载持有零件列表…</p>
      </div>

      <!-- 未识别工人 -->
      <div v-else-if="!worker?.id" class="empty-block">
        <el-icon :size="60" color="#e6a23c"><Warning /></el-icon>
        <h3>未识别工人</h3>
        <p>请重新刷工牌。</p>
        <el-button type="primary" @click="backToAction">返回</el-button>
      </div>

      <!-- 空列表 -->
      <div v-else-if="parts.length === 0" class="empty-block">
        <el-icon :size="60" color="#c0c4cc"><Box /></el-icon>
        <h3>您当前没有持有零件</h3>
        <p>请先到「取件」领取零件后再来放回。</p>
        <el-button @click="refresh" type="primary">刷新</el-button>
        <el-button @click="backToAction">返回</el-button>
      </div>

      <!-- 持有件列表 -->
      <div v-else>
        <div class="parts-header">
          <el-icon :size="24"><Box /></el-icon>
          <span class="parts-header-text">我的持有零件</span>
          <el-tag type="info" effect="plain" size="large" class="count-tag">
            共 {{ parts.length }} 件
          </el-tag>
          <el-button :icon="Refresh" circle size="small" @click="refresh" />
        </div>

        <!-- 已选确认栏 -->
        <div v-if="selectedPart" class="confirm-bar">
          <el-icon :size="20" color="#67c23a"><CircleCheckFilled /></el-icon>
          <span class="confirm-text">
            已选 <strong>{{ selectedPart.serial_no || selectedPart.drawing_no }}</strong>
            <el-tag v-if="selectedPart.batch_no" type="info" size="small" effect="plain">
              批次{{ selectedPart.batch_no }}
            </el-tag>
            · {{ selectedPart.name }}
            · 归还数量 {{ selectedPart.quantity }}
            · 下一工序：{{ selectedNextProcessName || '未选' }}
            · 待选货架
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

              <!-- 3) 数量 + 货架码 + 下一工序 -->
              <div class="part-line-bottom">
                <span class="qty">× {{ p.quantity }}</span>
                <span class="shelf-code-wrap">
                  <el-icon><Box /></el-icon>
                  <span>{{ p.shelf_code || '未上架' }}</span>
                </span>
                <span v-if="p.next_process_name" class="next-process">
                  · 下一工序：{{ p.next_process_name }}
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

    <!-- 下一道工序选择对话框（2026-07-17 升级为大卡 + INHOUSE/OUTSOURCE tabs） -->
    <ProcessPickerDialog
      v-if="showProcessDialog"
      v-model="showProcessDialog"
      kind="return"
      :current-process-id="selectedPart?.next_process_id ?? null"
      :exclude-process-ids="selectedPart?.next_process_id ? [selectedPart.next_process_id] : []"
      @confirm="onProcessPicked"
      @cancel="onProcessCancel"
    />

    <!-- 共享 HMI RETURN 货架选择卡片网格 picker -->
    <ShelfPickerDialog
      v-if="showShelfPicker"
      v-model="showShelfPicker"
      :next-process-id="selectedNextProcessId || ''"
      empty-action-label="重新选择工序"
      @confirm="onShelfConfirm"
      @cancel="onShelfCancel"
      @empty-action="onShelfEmpty"
    />

    <!-- 数量选择弹窗 -->
    <QuantityDialog
      v-if="showQtyDialog"
      v-model="showQtyDialog"
      :max="selectedPart?.quantity ?? 1"
      :serial-no="selectedPart?.serial_no || selectedPart?.drawing_no || null"
      :part-name="selectedPart?.name || null"
      action-label="放回"
      @confirm="onQtyConfirm"
      @cancel="cancelSelect"
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
  Avatar,
  Back,
  Box,
  CircleCheckFilled,
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
import { useScanBus } from '@/composables/useScanBus'
import { useScanPartsSort } from '@/composables/useScanPartsSort'
import HeldPartsBadge from '@/views/scan/components/HeldPartsBadge.vue'
import ScrollFabPair from '@/views/scan/components/ScrollFabPair.vue'
import QuantityDialog from '@/views/scan/components/QuantityDialog.vue'
import { listPartsHeldByWorker, scanPart, type PartItem } from '@/api/parts'
import ShelfPickerDialog from '@/views/scan/components/ShelfPickerDialog.vue'
import ProcessPickerDialog from '@/views/scan/components/ProcessPickerDialog.vue'
import BatchPickerDialog from '@/views/scan/components/BatchPickerDialog.vue'
import DeliveryDateChip from '@/views/scan/components/DeliveryDateChip.vue'
import type { Process } from '@/types/process'
import { findAllByCode, findPartBySerialAndPrompt } from '@/utils/scanHelpers'

const router = useRouter()
const { worker, requireWorker, reset: resetScanSession } = useScanSession()
const { onScan } = useBarcodeScanner()
const { emitHeldChanged } = useScanBus()

const parts = ref<PartItem[]>([])
// 「系统交期」硬优先级 + 原 is_urgent / planned_delivery_date 排序；详见 composable 注释
const sortedParts = useScanPartsSort(parts)
const loadingList = ref(false)
const selectedPart = ref<PartItem | null>(null)
const submitting = ref(false)

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

// 工序选择（2026-07-17：ProcessPickerDialog 自管加载与展示，这里只保留 select 后的状态）
const showProcessDialog = ref(false)
const selectedNextProcessId = ref<string>('')

const contentRef = ref<HTMLElement | null>(null)
const selectedNextProcessCode = ref<string>('')
const selectedNextProcessName = ref<string>('')

// 货架选择
const showShelfPicker = ref(false)
const showQtyDialog = ref(false)
const pendingShelfId = ref<string>('')

// --- 多批次扫码命中弹窗 ---
const showBatchPicker = ref(false)
const batchPickerCode = ref('')
const batchPickerRows = ref<PartItem[]>([])

// --- 扫码：扫描直接选中 + 滚动居中 + 触发 per-page tail；不在列表则提示当前位置 ---

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

/** RETURN tail：选中 + 清空 next_process_id（必须由弹窗显式选）+ 滚动 + 开工序选择弹窗 */
async function applyScanSelection(p: PartItem): Promise<void> {
  selectedPart.value = p
  selectedQty.value = p.quantity
  selectedNextProcessId.value = ''
  const key = String(p.batch_id || p.id)
  await scrollCardIntoView(key)
  showProcessDialog.value = true
}

async function onScanToSelect(rawCode: string): Promise<void> {
  const code = rawCode.trim()
  if (!code) return
  if (
    submitting.value ||
    showProcessDialog.value ||
    showShelfPicker.value ||
    showQtyDialog.value ||
    showBatchPicker.value
  ) return
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

const unsubScan = onScan((code) => { void onScanToSelect(code) })

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  await refresh()
})

onBeforeUnmount(() => {
  unsubScan()
  if (previewBlobUrl.value) URL.revokeObjectURL(previewBlobUrl.value)
})

async function refresh(): Promise<void> {
  if (!worker.value?.id) return
  loadingList.value = true
  try {
    parts.value = await listPartsHeldByWorker(String(worker.value.id))
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载持有零件列表失败')
    parts.value = []
  } finally {
    loadingList.value = false
  }
}

// --- 选件 → 工序 → 货架 → 提交 ---
const selectedQty = ref<number | undefined>(undefined)

/** 2026-07-29 批次化：行=批次，选中比较按 batch_id */
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
  selectedNextProcessId.value = ''
  showProcessDialog.value = true
}

// --- 预览 ---
async function onPreview(p: PartItem): Promise<void> {
  previewPart.value = p
  showPreview.value = true
  previewLoading.value = true
  const myToken = ++previewToken
  try {
    const files = await listPartFiles(String(p.id), 'DRAWING')
    if (myToken !== previewToken) return
    if (!files.length) {
      ElMessage.warning('暂无图纸')
      showPreview.value = false
      return
    }
    const f = files[0]
    previewFile.value = f
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

function onProcessPicked(process: Process): void {
  selectedNextProcessId.value = process.id
  selectedNextProcessCode.value = process.code
  selectedNextProcessName.value = `${process.code} ${process.name}`
  showProcessDialog.value = false
  showShelfPicker.value = true
}

function onProcessCancel(): void {
  showProcessDialog.value = false
  cancelSelect()
}

/**
 * 工人选了无映射架的工序 → ShelfPickerDialog 返空 → 点「重新选择工序」按钮。
 * 关闭 shelf picker，重新弹工艺序 picker 让工人换一个。
 */
function onShelfEmpty(): void {
  showShelfPicker.value = false
  showProcessDialog.value = true
}

async function onShelfConfirm(shelfId: string): Promise<void> {
  showShelfPicker.value = false
  if (!selectedPart.value || !selectedNextProcessId.value || !worker.value) {
    ElMessage.warning('选择已重置，请重新选择零件')
    return
  }
  pendingShelfId.value = shelfId
  showQtyDialog.value = true
}

async function onQtyConfirm(qty: number): Promise<void> {
  showQtyDialog.value = false
  if (!selectedPart.value || !selectedNextProcessId.value || !worker.value) {
    ElMessage.warning('选择已重置，请重新选择零件')
    return
  }
  selectedQty.value = qty
  submitting.value = true
  try {
    await scanPart({
      serial_no: selectedPart.value.serial_no ?? '',
      event_type: 'RETURNED',
      shelf_id: pendingShelfId.value,
      badge_code: worker.value.badge_code ?? '',
      next_process_id: selectedNextProcessId.value,
      batch_id: selectedPart.value.batch_id ?? null,
      quantity: qty,
    })
    ElMessage.success(
      `已放回：${selectedPart.value.serial_no} → ${
        selectedNextProcessName.value ?? ''
      }`,
    )
    cancelSelect()
    await refresh()
    emitHeldChanged()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '放回失败')
  } finally {
    submitting.value = false
  }
}

function onShelfCancel(): void {
  showShelfPicker.value = false
  cancelSelect()
}

function cancelSelect(): void {
  selectedPart.value = null
  selectedQty.value = undefined
  selectedNextProcessId.value = ''
  pendingShelfId.value = ''
}

function backToAction(): void {
  cancelSelect()
  void router.replace('/scan/action')
}

function backToBadge(): void {
  cancelSelect()
  resetScanSession()
  void router.replace('/scan/badge')
}
</script>

<style lang="scss" scoped>
.scan-return {
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
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 8px;
  margin-bottom: 16px;
}
.confirm-text { flex: 1; color: #303133; }
.confirm-text strong {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: #67c23a; font-size: 18px; margin: 0 4px;
}

.parts-list { display: flex; flex-direction: column; gap: 10px; }

.part-row {
  display: flex !important;
  align-items: stretch;
  padding: 14px 18px !important;
  border: 1px solid #e4e7ed;
  border-left: 4px solid #e6a23c;  // 放回流程强调橙黄（与取件蓝区分）
  border-radius: 8px;
  cursor: pointer;
  position: relative;
  background: #fff;
  transition: border-color .15s, background .15s, box-shadow .15s;
}
.part-row:hover {
  box-shadow: 0 2px 12px rgba(230, 162, 60, .08);
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

.part-row-main { display: flex; flex-direction: column; gap: 6px; width: 100%; min-width: 0; }
.preview-btn { position: absolute !important; top: 8px; right: 10px; z-index: 1; }

.part-line-top { display: flex; align-items: center; gap: 12px; padding-right: 64px; flex-wrap: wrap; }
.serial-no {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 22px; font-weight: 700; color: #303133; letter-spacing: 0.5px;
}
/* .delivery-date / .days-left / .overdue / .due-soon 已迁至 components/DeliveryDateChip.vue */

.part-line-name { display: flex; align-items: center; gap: 10px; font-size: 15px; color: #303133; }
.part-name { font-weight: 500; color: #303133; }
.customer { color: #909399; font-size: 13px; }

.part-line-bottom { display: flex; align-items: center; gap: 16px; font-size: 14px; color: #606266; flex-wrap: wrap; }
.qty { color: #e6a23c; font-weight: 700; font-size: 15px; }
.shelf-code-wrap { display: inline-flex; align-items: center; gap: 4px; color: #909399; }
.next-process { color: #67c23a; }

.is-loading { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

@keyframes urgentPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.urgent-pulse {
  animation: urgentPulse 1.2s ease-in-out infinite;
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
