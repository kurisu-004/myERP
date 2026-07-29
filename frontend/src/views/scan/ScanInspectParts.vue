<!--
  ScanInspectParts.vue

  /scan/inspect —— 扫码台 INSPECT 新流程（2026-07-19，仿 ScanReturnParts.vue 范式）
  1. onBeforeMount 调 GET /parts/by-worker/{worker_id} 列出当前 worker 持有件
  2. 工人点选一件 → 进入「待扫码确认」状态（confirm-bar 提示扫该件条码）
  3. 扫码枪扫到与选中件 serial_no 匹配的条码 → 弹 ShelfPickerDialog(kind=inspection)
     （与 ScanPickParts.vue 的「选中后扫码确认」同款防误触模式；不匹配 → ElMessage.error）
  4. 送检只需指定品检货架，不需要下一道工序（后端 INSPECTED 忽略 next_process_id）
  5. 提交 POST /parts/scan (event_type=INSPECTED, shelf_id=target_inspection_shelf_id=选中品检架)
     —— shelf_id 供 require_shelf_account_from_body 做货架权限校验，
        target_inspection_shelf_id 供 service 校验 zone=INSPECTION；
        两处都填同一个选中架即可（picker 只列本账号 scope 内的品检架）
  6. 成功后自动 refresh（该件从列表消失）

  旧「扫一批条码」流程（ScanPartsWork.vue ?action=inspect）已随本页上线替换不保留。
-->

<template>
  <div class="scan-inspect">
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
        <el-tag type="success" effect="dark">送 检</el-tag>
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
        <p>请先到「取件」领取零件后再来送检。</p>
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

        <!-- 待扫码确认栏 -->
        <div v-if="selectedPart && awaitingScan" class="confirm-bar pending-scan">
          <el-icon :size="20" color="#e6a23c"><Aim /></el-icon>
          <span class="confirm-text">
            已选 <strong>{{ selectedPart.serial_no || selectedPart.drawing_no }}</strong>
            <el-tag v-if="selectedPart.batch_no" type="info" size="small" effect="plain">
              批次{{ selectedPart.batch_no }}
            </el-tag>
            · {{ selectedPart.name }}
            · 送检数量
            <el-input-number
              v-model="selectedQty"
              :min="1"
              :max="selectedPart.quantity"
              :precision="0"
              size="small"
              class="qty-input"
            />
            / {{ selectedPart.quantity }}
            · 请<strong>扫描该零件条码</strong>确认送检
          </span>
          <el-button size="small" @click="cancelSelect">取消选择</el-button>
        </div>

        <div class="parts-list">
          <el-card
            v-for="p in parts"
            :key="p.batch_id || p.id"
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
                <span class="delivery-date" :class="deliveryUrgencyClass(p.planned_delivery_date)">
                  <el-icon><Calendar /></el-icon>
                  {{ formatDate(p.planned_delivery_date) }}
                  <span v-if="daysLeftText(p.planned_delivery_date)" class="days-left">
                    · {{ daysLeftText(p.planned_delivery_date) }}
                  </span>
                </span>
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

    <!-- 2026-07-26：工控机触屏友好 — 右下角两个浮动滚动按钮（touch 友好） -->
    <div v-if="parts.length > 1" class="scroll-fab">
      <el-button
        type="primary"
        circle
        size="large"
        :disabled="atTop"
        aria-label="滚动到顶部"
        @click="scrollToTop"
      >
        <el-icon><ArrowUp /></el-icon>
      </el-button>
      <el-button
        type="primary"
        circle
        size="large"
        :disabled="atBottom"
        aria-label="滚动到底部"
        @click="scrollToBottom"
      >
        <el-icon><ArrowDown /></el-icon>
      </el-button>
    </div>

    <!-- 品检货架选择（送检只需选架，不需下一道工序） -->
    <ShelfPickerDialog
      v-if="showShelfPicker"
      v-model="showShelfPicker"
      kind="inspection"
      empty-action-label="取消选择"
      @confirm="onShelfConfirm"
      @cancel="onShelfCancel"
      @empty-action="onShelfEmpty"
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
import { computed, onBeforeMount, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Aim,
  ArrowDown,
  ArrowUp,
  Avatar,
  Back,
  Box,
  Calendar,
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
import HeldPartsBadge from '@/views/scan/components/HeldPartsBadge.vue'
import { listPartsHeldByWorker, scanPart, type PartItem } from '@/api/parts'
import ShelfPickerDialog from '@/views/scan/components/ShelfPickerDialog.vue'

const router = useRouter()
const { worker, requireWorker, reset: resetScanSession } = useScanSession()
const { onScan } = useBarcodeScanner()
const { emitHeldChanged } = useScanBus()

const parts = ref<PartItem[]>([])
const loadingList = ref(false)
const selectedPart = ref<PartItem | null>(null)
// 点选卡片后进入「待扫码确认」状态；扫到匹配条码才弹送检货架 picker
const awaitingScan = ref(false)

// --- 2026-07-26：工控机触屏友好 — 右下浮动滚动按钮的状态与控制 ---
const contentRef = ref<HTMLElement | null>(null)
const atTop = ref(true)
const atBottom = ref(false)
function onContentScroll(): void {
  const el = contentRef.value
  if (!el) return
  atTop.value = el.scrollTop <= 1
  atBottom.value = el.scrollTop + el.clientHeight >= el.scrollHeight - 1
}
function scrollToTop(): void {
  contentRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
}
function scrollToBottom(): void {
  const el = contentRef.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}
onMounted(() => {
  contentRef.value?.addEventListener('scroll', onContentScroll, { passive: true })
  onContentScroll()
})
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

// 货架选择
const showShelfPicker = ref(false)

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  await refresh()
})

// 全局扫码订阅：仅在「待扫码确认」状态下消费，其余忽略（防误扫）
const unsubScan = onScan((code) => {
  onScanCode(code)
})

onBeforeUnmount(() => {
  unsubScan()
  contentRef.value?.removeEventListener('scroll', onContentScroll)
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

// --- 选件 → 扫码确认 → 选品检架 → 提交 ---
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
  awaitingScan.value = true
}

/**
 * 扫码确认（与 ScanPickParts.vue::onScanCode 同款模式）：
 * 只有已选中件且处于 awaitingScan 时才消费；条码与选中件 serial_no 精确匹配
 * 才弹送检货架 picker，否则报错让工人重扫。
 */
function onScanCode(rawCode: string): void {
  const code = rawCode.trim()
  if (!code) return
  if (submitting.value || showShelfPicker.value) return
  if (!selectedPart.value || !awaitingScan.value) return
  const expect = selectedPart.value.serial_no || selectedPart.value.drawing_no
  if (code !== expect) {
    ElMessage.error(`扫码与选中件不匹配 (期望 ${expect}, 扫到 ${code})`)
    return
  }
  awaitingScan.value = false
  showShelfPicker.value = true
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

async function onShelfConfirm(shelfId: string): Promise<void> {
  showShelfPicker.value = false
  if (!selectedPart.value || !worker.value) {
    ElMessage.warning('选择已重置，请重新选择零件')
    return
  }
  submitting.value = true
  try {
    await scanPart({
      serial_no: selectedPart.value.serial_no ?? '',
      event_type: 'INSPECTED',
      // shelf_id（权限校验：操作所在架）与 target_inspection_shelf_id（送检目标架）
      // 都填选中的品检架：picker 只列本 SHELF_ACCOUNT scope 内的品检架，两处校验都能过。
      shelf_id: shelfId,
      badge_code: worker.value.badge_code ?? '',
      target_inspection_shelf_id: shelfId,
      batch_id: selectedPart.value.batch_id ?? null,
      quantity: selectedQty.value ?? null,
    })
    ElMessage.success(`已送检：${selectedPart.value.serial_no}`)
    cancelSelect()
    await refresh()
    emitHeldChanged()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '送检失败')
  } finally {
    submitting.value = false
  }
}

function onShelfCancel(): void {
  showShelfPicker.value = false
  cancelSelect()
}

/** 品检架 picker 空状态「取消选择」：关弹窗并清空选中，工人可重新点选。 */
function onShelfEmpty(): void {
  showShelfPicker.value = false
  cancelSelect()
}

function cancelSelect(): void {
  selectedPart.value = null
  selectedQty.value = undefined
  awaitingScan.value = false
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

// --- 交期辅助（与 ScanReturnParts 一致） ---
function formatDate(s: string | null | undefined): string {
  if (!s) return ''
  return s.slice(5).replace(/-/g, '/')  // MM/DD
}
function daysLeftText(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return `已逾期${Math.abs(diff)}天`
  if (diff === 0) return '今天到期'
  if (diff <= 3) return `${diff}天后到期`
  return ''
}
function deliveryUrgencyClass(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'overdue'
  if (diff <= 3) return 'due-soon'
  return ''
}
</script>

<style lang="scss" scoped>
.scan-inspect {
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
  border-radius: 8px;
  margin-bottom: 16px;
}
/* 待扫码确认：琥珀底提示工人动作未完成（与放回页的绿底「已确认」区分） */
.confirm-bar.pending-scan {
  background: #fdf6ec;
  border: 1px solid #faecd8;
}
.confirm-text { flex: 1; color: #303133; }
.confirm-text strong {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: #e6a23c; font-size: 18px; margin: 0 4px;
}

.parts-list { display: flex; flex-direction: column; gap: 10px; }

.part-row {
  display: flex !important;
  align-items: stretch;
  padding: 14px 18px !important;
  border: 1px solid #e4e7ed;
  border-left: 4px solid #67c23a;  // 送检流程强调绿色（与放回橙、取件蓝区分）
  border-radius: 8px;
  cursor: pointer;
  position: relative;
  background: #fff;
  transition: border-color .15s, background .15s, box-shadow .15s;
}
.part-row:hover {
  box-shadow: 0 2px 12px rgba(103, 194, 58, .08);
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
.delivery-date {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 16px; font-weight: 600;
  padding: 2px 10px; border-radius: 4px;
  background: #f5f7fa; color: #606266;
}
.delivery-date.overdue { color: #f56c6c; background: #fef0f0; }
.delivery-date.due-soon { color: #e6a23c; background: #fdf6ec; }
.days-left { font-weight: 500; font-size: 13px; margin-left: 2px; }

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

// 2026-07-26：右下浮动滚动按钮（工控机触屏拖页不便）
.scroll-fab {
  position: fixed;
  right: 24px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 100;
}
.scroll-fab .el-button {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}
.qty-input {
  width: 110px;
  vertical-align: middle;
  margin: 0 2px;
}
</style>
