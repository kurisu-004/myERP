<!--
  FileListCard.vue

  通用「文件列表 + 上传/删除/预览」卡片组件（2026-07-10 重写，2026-07-14 扩展）。

  用 kind 字段区分文件类型：
  - DRAWING           零件 / 子件 图纸
                        (PDF + 9 种图片：PNG/JPG/JPEG/GIF/BMP/TIF/TIFF/WEBP/HEIC)
                        图片与 PDF 同槽（单文件覆盖语义），打印背面要打条码
  - 3D_MODEL          零件 3D 模型
                        (STEP/STP/IGES/IGS/STL/OBJ/3MF)
  - G_CODE            零件 CNC G 代码（NC / TAP / CNC / MPF / NGC）
  - SETUP_SHEET       零件 CNC 设定单（PDF）
  - ASSEMBLY_MASTER   装配体总装图（PDF）
  - CAD_2D            零件 CAD 源文件（DWG / DXF）——2026-07-14 新增

  Props：
  - files:               PartFileItem[]
  - ownerType:           'assembly' | 'part'
  - ownerId:             string (雪花 ID 字符串)
  - defaultPage?:        number  默认 1
  - showUpload?:         boolean 默认 false
  - showDelete?:         boolean 默认 false
  - showPrint?:          boolean 默认 false（仅对 ownerType='part' 生效）
  - kind?:               PartFileKind 默认 'DRAWING'（决定 ACCEPT 与 title）
  - title?:              string   默认按 kind 显示
  - accept?:             string   默认按 kind 决定
  - emptyText?:          string   默认按 kind 显示
  - uploadLabel?:        string   默认 '上传' / '替换'（按 files.length 自动）
  - apiUpload?:          (id, file) => Promise<PartFileItem>   必填（自定义 endpoint）
  - apiDelete?:          (id) => Promise<void>                 默认走全局 deleteFile
-->
<template>
  <el-card shadow="never" class="files-card">
    <template #header>
      <div class="card-header">
        <span class="card-title">
          {{ titleText }}
          <el-tag v-if="files.length > 0" type="info" size="small" effect="plain">
            {{ files.length }} 个
          </el-tag>
        </span>
        <div class="header-actions">
          <el-button
            v-if="showPrint && ownerType === 'part'"
            type="success"
            plain
            :loading="printing"
            @click="onPrint"
          >
            <el-icon><Printer /></el-icon>
            <span>打印图纸（含条形码）</span>
          </el-button>
          <el-upload
            v-if="showUpload"
            :show-file-list="false"
            :auto-upload="false"
            :on-change="onPick"
            :accept="ACCEPT"
          >
            <el-button type="primary" plain :loading="uploading">
              <el-icon><Upload /></el-icon>
              <span>{{ uploadLabelText }}</span>
            </el-button>
          </el-upload>
        </div>
      </div>
    </template>

    <div v-if="files.length === 0" class="empty-tip">
      <el-icon :size="32" color="#c0c4cc"><DocumentRemove /></el-icon>
      <p>{{ emptyTextText }}</p>
    </div>

    <div v-else class="file-grid">
      <div
        v-for="f in files"
        :key="f.id"
        class="file-item"
        :class="{ 'is-active': previewFile?.id === f.id }"
        @click="onPreview(f)"
      >
        <el-icon :size="28" :color="iconColor(f.file_type)">
          <component :is="iconOf(f.file_type)" />
        </el-icon>
        <div class="file-meta">
          <div class="file-name" :title="f.original_filename">
            {{ f.original_filename }}
          </div>
          <div class="file-sub">
            <el-tag size="small" effect="plain">{{ f.file_type }}</el-tag>
            <span>{{ formatSize(f.file_size) }}</span>
          </div>
        </div>
        <el-button
          v-if="showDelete"
          link
          type="danger"
          size="small"
          class="del-btn"
          @click.stop="onDelete(f)"
        >
          <el-icon><Delete /></el-icon>
        </el-button>
      </div>
    </div>

    <!-- 预览弹窗（全屏）：PDF 用 PdfViewer；图片用 el-image；其它走下载提示 -->
    <el-dialog
      v-model="previewVisible"
      :title="previewTitle"
      fullscreen
      :close-on-click-modal="false"
      destroy-on-close
      @closed="onPreviewClosed"
    >
      <PdfViewer
        v-if="previewFile && isPdf(previewFile.file_type)"
        :url="previewBlobUrl"
        :page="defaultPage"


      />
      <div
        v-else-if="previewFile && isImage(previewFile.file_type)"
        class="image-preview-wrap"
      >
        <!-- 2026-07-14：HEIC 不被浏览器支持 → 走下载；其它图片走 el-image 全屏预览 -->
        <el-image
          v-if="!isHeic(previewFile.file_type)"
          :src="previewBlobUrl"
          :preview-src-list="[previewBlobUrl]"
          :initial-index="0"
          fit="contain"
          style="max-width: 100%; max-height: calc(100vh - 80px);"
        />
        <div v-else class="non-pdf-preview">
          <el-icon :size="48" :color="iconColor(previewFile.file_type)">
            <component :is="iconOf(previewFile.file_type)" />
          </el-icon>
          <p class="non-pdf-name">{{ previewFile.original_filename }}</p>
          <p class="non-pdf-hint">
            HEIC 格式浏览器不直接支持预览，请下载后查看。
          </p>
          <el-button type="primary" @click="downloadCurrent">
            <el-icon><Download /></el-icon>
            <span>下载文件</span>
          </el-button>
        </div>
      </div>
      <div v-else class="non-pdf-preview">
        <el-icon :size="48" :color="iconColor(previewFile?.file_type || '')">
          <component :is="iconOf(previewFile?.file_type || '')" />
        </el-icon>
        <p class="non-pdf-name">{{ previewFile?.original_filename }}</p>
        <p class="non-pdf-hint">
          {{ previewFile?.file_type }} 文件不支持浏览器内嵌预览，请下载后查看。
        </p>
        <el-button type="primary" @click="downloadCurrent">
          <el-icon><Download /></el-icon>
          <span>下载文件</span>
        </el-button>
      </div>
    </el-dialog>

    <!-- 打印用隐藏 iframe -->
    <iframe
      ref="printIframeRef"
      style="position: fixed; right: 0; bottom: 0; width: 1px; height: 1px; border: 0; opacity: 0; pointer-events: none;"
      title="打印预览"
    />
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  DocumentRemove,
  Download,
  Picture,
  Files,
  Printer,
  Upload,
} from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import PdfViewer from './PdfViewer.vue'
import { api } from '@/api/http'
import { deleteFile, getDownloadUrl } from '@/api/assembly'
import { printPartDrawing } from '@/api/parts'
import type { PartFileItem, PartFileKind } from '@/types/part_file'

// ----- ACCEPT 与 title 按 kind 自动派生 -----
// 2026-07-14：DRAWING 加 9 种图片（PNG/JPG/.../HEIC，与 PDF 同槽）；
// 3D_MODEL 加 IGES/STL/OBJ/3MF；新增 CAD_2D (DWG/DXF)
const ACCEPT_BY_KIND: Record<PartFileKind, string> = {
  DRAWING:
    '.pdf,.png,.jpg,.jpeg,.gif,.bmp,.tif,.tiff,.webp,.heic',
  '3D_MODEL': '.step,.stp,.iges,.igs,.stl,.obj,.3mf',
  G_CODE: '.nc,.tap,.cnc,.mpf,.ngc',
  SETUP_SHEET: '.pdf',
  ASSEMBLY_MASTER: '.pdf',
  CAD_2D: '.dwg,.dxf',
}

const TITLE_BY_KIND: Record<PartFileKind, string> = {
  DRAWING: '图纸',
  '3D_MODEL': '3D 模型',
  G_CODE: 'G 代码',
  SETUP_SHEET: 'CNC 设定单',
  ASSEMBLY_MASTER: '总装图',
  CAD_2D: 'CAD 源文件',
}

const EMPTY_TEXT_BY_KIND: Record<PartFileKind, string> = {
  DRAWING: '暂无图纸',
  '3D_MODEL': '暂无 3D 模型',
  G_CODE: '暂无 G 代码',
  SETUP_SHEET: '暂无 CNC 设定单',
  ASSEMBLY_MASTER: '暂无总装图',
  CAD_2D: '暂无 CAD 源文件',
}

const UPLOAD_LABEL_BY_KIND: Record<PartFileKind, string> = {
  DRAWING: '图纸',
  '3D_MODEL': '3D 模型',
  G_CODE: 'G 代码',
  SETUP_SHEET: '设定单',
  ASSEMBLY_MASTER: '总装图',
  CAD_2D: 'CAD 源文件',
}

interface Props {
  files: PartFileItem[]
  ownerType: 'assembly' | 'part'
  ownerId: string
  defaultPage?: number
  showUpload?: boolean
  showDelete?: boolean
  showPrint?: boolean
  kind?: PartFileKind
  title?: string
  accept?: string
  emptyText?: string
  /** 自定义上传函数（用于不同 kind 走不同 endpoint） */
  apiUpload?: (ownerId: string, file: File) => Promise<PartFileItem>
  /** 自定义删除函数（默认走 /files/{id}/delete） */
  apiDelete?: (fileId: string) => Promise<void>
}
const props = withDefaults(defineProps<Props>(), {
  defaultPage: 1,
  showUpload: false,
  showDelete: false,
  showPrint: false,
  kind: 'DRAWING',
  title: '',
  accept: '',
  emptyText: '',
})

const emit = defineEmits<{
  uploaded: [PartFileItem]
  deleted: [string]
  refresh: []
}>()

const ACCEPT = computed<string>(() =>
  props.accept || ACCEPT_BY_KIND[props.kind],
)
const titleText = computed<string>(() =>
  props.title || TITLE_BY_KIND[props.kind],
)
const emptyTextText = computed<string>(() =>
  props.emptyText || EMPTY_TEXT_BY_KIND[props.kind],
)
const uploadLabelText = computed<string>(() => {
  const base = UPLOAD_LABEL_BY_KIND[props.kind]
  return `${files.value.length > 0 ? '替换' : '上传'}${base}`
})

const uploading = ref(false)
const previewVisible = ref(false)
const previewFile = ref<PartFileItem | null>(null)
const previewBlobUrl = ref<string>('')
const files = computed<PartFileItem[]>(() => props.files)

const previewTitle = computed<string>(
  () => `预览 — ${previewFile.value?.original_filename ?? ''}`,
)

function isPdf(t: string): boolean {
  return t.toUpperCase() === 'PDF'
}
// 2026-07-14：DRAWING 扩 9 种图片格式
const IMAGE_TYPES = new Set([
  'PNG', 'JPG', 'JPEG', 'GIF', 'BMP', 'TIF', 'TIFF', 'WEBP',
])
function isImage(t: string): boolean {
  return IMAGE_TYPES.has(t.toUpperCase())
}
function isHeic(t: string): boolean {
  return t.toUpperCase() === 'HEIC'
}
function iconOf(t: string) {
  const up = t.toUpperCase()
  if (up === 'PDF') return Picture
  if (IMAGE_TYPES.has(up)) return Picture
  return Files
}
function iconColor(t: string): string {
  const up = t.toUpperCase()
  if (up === 'PDF') return '#e15c5c'
  if (IMAGE_TYPES.has(up)) return '#67c23a'  // 图片：绿色
  if (up === 'STEP' || up === 'STP' || up === 'IGES' || up === 'IGS') return '#3a7bd5'
  if (up === 'STL' || up === 'OBJ' || up === '3MF') return '#3a7bd5'
  if (up === 'DWG' || up === 'DXF') return '#ff9800'
  return '#909399'
}
function formatSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}

async function onPick(uploadFile: UploadFile): Promise<void> {
  if (!uploadFile.raw) return
  if (!props.apiUpload) {
    ElMessage.error('FileListCard 未配置 apiUpload，无法上传')
    return
  }
  uploading.value = true
  try {
    const result = await props.apiUpload(props.ownerId, uploadFile.raw)
    ElMessage.success(`已上传：${result.original_filename}`)
    emit('uploaded', result)
    emit('refresh')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '上传失败')
  } finally {
    uploading.value = false
  }
}

async function onPreview(f: PartFileItem): Promise<void> {
  previewFile.value = f
  previewVisible.value = true
  try {
    const resp = await api.get(`/files/${f.id}/content`, { responseType: 'blob' })
    if (previewBlobUrl.value) URL.revokeObjectURL(previewBlobUrl.value)
    previewBlobUrl.value = URL.createObjectURL(resp.data)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载文件失败')
  }
}

function onPreviewClosed(): void {
  if (previewBlobUrl.value) {
    URL.revokeObjectURL(previewBlobUrl.value)
    previewBlobUrl.value = ''
  }
}

async function downloadCurrent(): Promise<void> {
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

async function onDelete(f: PartFileItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除「${f.original_filename}」？删除后文件仍可从 COS 重新下载，但前端不再列出。`,
      '删除文件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    if (props.apiDelete) {
      await props.apiDelete(f.id)
    } else {
      await deleteFile(f.id)
    }
    ElMessage.success('已删除')
    emit('deleted', f.id)
    emit('refresh')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

// ============================================================
// 双面打印：仅对 ownerType='part' 生效
// ============================================================
const printing = ref(false)
const printIframeRef = ref<HTMLIFrameElement | null>(null)
let printBlobUrl = ''

async function onPrint(): Promise<void> {
  if (props.ownerType !== 'part') return
  printing.value = true
  try {
    const blob = await printPartDrawing(props.ownerId)
    if (printBlobUrl) URL.revokeObjectURL(printBlobUrl)
    printBlobUrl = URL.createObjectURL(blob)

    const iframe = printIframeRef.value
    if (!iframe) {
      ElMessage.error('打印 iframe 未挂载，请刷新页面后重试')
      return
    }
    iframe.src = printBlobUrl
    iframe.onload = () => {
      try {
        iframe.contentWindow?.focus()
        iframe.contentWindow?.print()
      } catch {
        const w = window.open(printBlobUrl, '_blank')
        if (w) w.print()
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '生成打印 PDF 失败')
  } finally {
    setTimeout(() => {
      printing.value = false
    }, 800)
  }
}

onBeforeUnmount(() => {
  if (printBlobUrl) URL.revokeObjectURL(printBlobUrl)
})
</script>

<style lang="scss" scoped>
.files-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  color: var(--text-secondary);
  gap: 8px;
  p {
    margin: 0;
    font-size: 13px;
  }
}
.file-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}
.file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
  position: relative;

  &:hover {
    border-color: var(--primary-color);
    box-shadow: 0 2px 8px rgba(30, 77, 139, 0.08);
  }
  &.is-active {
    border-color: var(--primary-color);
    background: var(--primary-bg);
  }
}
.file-meta {
  flex: 1;
  min-width: 0;
}
.file-name {
  font-size: 13px;
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}
.del-btn {
  flex-shrink: 0;
}
.non-pdf-preview {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 32px;
}
.image-preview-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 80px);
  padding: 24px;
  background: #1e1e1e;
}
.non-pdf-name {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
.non-pdf-hint {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}
</style>