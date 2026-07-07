<!--
  FileListCard.vue

  显示某 part / assembly 关联的所有图纸文件，支持：
  - PDF 内嵌预览（多页 PDF 跳转到当前 part 对应的 page_index）
  - STEP/DWG/DXF 等只显示文件名 + 下载链接
  - 上传按钮（multipart，调用后端 /v1/{parts|assemblies}/{id}/files）
  - 删除按钮（软删，COS 清理由后端异步做）

  Props:
  - files: DrawingFileItem[]
  - ownerType: 'assembly' | 'part'
  - ownerId: number
  - defaultPage?: number
  - showUpload / showDelete: boolean
-->
<template>
  <el-card shadow="never" class="files-card">
    <template #header>
      <div class="card-header">
        <span class="card-title">
          图纸 / 文件
          <el-tag v-if="files.length > 0" type="info" size="small" effect="plain">
            {{ files.length }} 个
          </el-tag>
        </span>
        <div class="header-actions">
          <!-- 打印按钮：仅零件可见，用于触发双面打印 PDF（图纸 + 反面条形码） -->
          <el-button
            v-if="showPrint"
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
              <span>上传文件</span>
            </el-button>
          </el-upload>
        </div>
      </div>
    </template>

    <div v-if="files.length === 0" class="empty-tip">
      <el-icon :size="32" color="#c0c4cc"><DocumentRemove /></el-icon>
      <p>暂无图纸 / 文件</p>
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
            <span v-if="f.page_index != null" class="page-tag">
              第 {{ f.page_index }} 页
            </span>
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

    <!-- PDF 预览弹窗（全屏） -->
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
        :page="previewFile.page_index ?? defaultPage ?? 1"
        :initial-scale="1.4"
      />
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

    <!-- 打印用隐藏 iframe（src 注入 PDF blob URL，触发浏览器打印） -->
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
import { deleteFile, getDownloadUrl, uploadAssemblyFile, uploadPartFile } from '@/api/assembly'
import { printPartDrawing } from '@/api/parts'
import type { DrawingFileItem } from '@/types/file'

interface Props {
  files: DrawingFileItem[]
  ownerType: 'assembly' | 'part'
  /** 后端 IdStr 序列化为字符串；雪花 ID 完整保留 */
  ownerId: string
  defaultPage?: number
  showUpload?: boolean
  showDelete?: boolean
  /** 显示「打印图纸（含条形码）」按钮；仅对 ownerType='part' 生效 */
  showPrint?: boolean
}
const props = withDefaults(defineProps<Props>(), {
  defaultPage: 1,
  showUpload: false,
  showDelete: false,
  showPrint: false,
})

const emit = defineEmits<{
  uploaded: [DrawingFileItem]
  deleted: [string]
  refresh: []
}>()

const ACCEPT = '.pdf,.step,.stp,.dwg,.dxf'
const uploading = ref(false)
const previewVisible = ref(false)
const previewFile = ref<DrawingFileItem | null>(null)
const previewBlobUrl = ref<string>('')

const previewTitle = computed<string>(
  () => `预览 — ${previewFile.value?.original_filename ?? ''}`,
)

function isPdf(t: string): boolean {
  return t.toUpperCase() === 'PDF'
}
function iconOf(t: string) {
  const up = t.toUpperCase()
  if (up === 'PDF') return Picture
  return Files
}
function iconColor(t: string): string {
  const up = t.toUpperCase()
  if (up === 'PDF') return '#e15c5c'
  if (up === 'STEP' || up === 'STP') return '#3a7bd5'
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
  uploading.value = true
  try {
    const result: DrawingFileItem =
      props.ownerType === 'assembly'
        ? await uploadAssemblyFile(props.ownerId, uploadFile.raw)
        : await uploadPartFile(props.ownerId, uploadFile.raw)
    ElMessage.success(`已上传：${result.original_filename}`)
    emit('uploaded', result)
    emit('refresh')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '上传失败')
  } finally {
    uploading.value = false
  }
}

async function onPreview(f: DrawingFileItem): Promise<void> {
  previewFile.value = f
  previewVisible.value = true
  // 通过 axios 拉取文件内容（带上 Authorization header），生成 blob URL 给 pdfjs
  try {
    const resp = await api.get(`/drawings/${f.id}/content`, { responseType: 'blob' })
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

async function onDelete(f: DrawingFileItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除「${f.original_filename}」？删除后 PDF 仍可从 COS 重新下载，但前端不再列出。`,
      '删除文件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteFile(f.id)
    ElMessage.success('已删除')
    emit('deleted', f.id)
    emit('refresh')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

// ============================================================
// 双面打印：拉后端生成的 PDF（图纸 + 反面右下角条形码）→ 触发浏览器打印
// ============================================================
const printing = ref(false)
const printIframeRef = ref<HTMLIFrameElement | null>(null)
let printBlobUrl = ''

async function onPrint(): Promise<void> {
  if (props.ownerType !== 'part') return
  printing.value = true
  try {
    const blob = await printPartDrawing(props.ownerId)
    // 清理上一次的 blob URL（避免内存泄漏）
    if (printBlobUrl) URL.revokeObjectURL(printBlobUrl)
    printBlobUrl = URL.createObjectURL(blob)

    // 用隐藏 iframe 加载 PDF，触发打印对话框；
    // 比 window.open 更好：不被弹窗拦截，且打印对话框自然出现。
    const iframe = printIframeRef.value
    if (!iframe) {
      ElMessage.error('打印 iframe 未挂载，请刷新页面后重试')
      return
    }
    iframe.src = printBlobUrl
    // 等待 PDF 加载完成后调 print
    iframe.onload = () => {
      try {
        iframe.contentWindow?.focus()
        iframe.contentWindow?.print()
      } catch {
        // 某些浏览器 sandbox 限制 — fallback：开新窗口
        const w = window.open(printBlobUrl, '_blank')
        if (w) w.print()
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '生成打印 PDF 失败')
  } finally {
    // 留几秒给打印对话框弹出再清 loading
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
.page-tag {
  background: #f0f7ff;
  color: var(--primary-color);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
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
