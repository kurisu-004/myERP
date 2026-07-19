<!--
  PdfViewer.vue

  PDF 内嵌预览组件（基于 pdfjs-dist）。
  接收一个 url（COS 临时签名 URL，由后端即时签发），直接 GET 拉取 PDF 二进制并渲染。
-->
<template>
  <div class="pdf-viewer">
    <div v-if="loading" class="loading">
      <el-icon :size="24" class="is-loading"><Loading /></el-icon>
      <span>加载中…</span>
    </div>
    <div v-else-if="error" class="error">
      <el-icon :size="24" color="#f56c6c"><CircleClose /></el-icon>
      <span>{{ error }}</span>
    </div>
    <div v-else ref="wrapRef" class="canvas-wrap">
      <div class="pdf-toolbar">
        <el-button-group>
          <el-button :disabled="page <= 1" @click="prevPage">
            <el-icon><ArrowLeft /></el-icon>
            <span>上一页</span>
          </el-button>
          <el-button :disabled="page >= totalPages" @click="nextPage">
            <span>下一页</span>
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </el-button-group>
        <span class="page-info">{{ page }} / {{ totalPages }}</span>
        <el-input-number
          v-model="scale"
          :min="0.4"
          :max="3"
          :step="0.2"
          :precision="1"
          size="small"
          controls-position="right"
          @change="render"
        />
        <el-button @click="zoomIn"><el-icon><ZoomIn /></el-icon></el-button>
        <el-button @click="zoomOut"><el-icon><ZoomOut /></el-icon></el-button>
        <el-button @click="download">
          <el-icon><Download /></el-icon>
          <span>下载</span>
        </el-button>
      </div>
      <canvas ref="canvasRef" class="pdf-canvas" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import {
  ArrowLeft,
  ArrowRight,
  CircleClose,
  Download,
  Loading,
  ZoomIn,
  ZoomOut,
} from '@element-plus/icons-vue'
import { pdfjsLib } from '@/utils/pdfjs'

interface Props {
  url: string
  page?: number
  initialScale?: number
  /** 首次渲染时缩放至容器大小（取 fitScale 与 initialScale 的较小值，
   *  避免在 initialScale 已经够小时放大；用户手动缩放后保持用户选择）。 */
  fit?: boolean
}
const props = withDefaults(defineProps<Props>(), {
  page: 1,
  initialScale: 1.4,
  fit: false,
})

const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const page = ref(props.page)
const totalPages = ref(0)
const scale = ref(props.initialScale)

let pdfDoc: pdfjsLib.PDFDocumentProxy | null = null
let renderTask: pdfjsLib.RenderTask | null = null

async function load() {
  if (!props.url) return
  loading.value = true
  error.value = null
  try {
    const task = pdfjsLib.getDocument({ url: props.url })
    pdfDoc = await task.promise
    totalPages.value = pdfDoc.numPages
    if (page.value < 1) page.value = 1
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (e) {
    error.value = (e as Error).message ?? 'PDF 加载失败'
    loading.value = false
    return
  }
  // 先让 Vue 把 canvas-wrap（含 canvas）插入 DOM，
  // 否则 render() 中 canvasRef.value 为 null，绘画被静默跳过。
  loading.value = false
  await nextTick()
  // fit=true 时：测量 wrap 容器大小，取 min(initialScale, fitScale)
  // 在 render() 之前设置 scale.value，避免先按 initialScale 闪一下再跳到 fit。
  if (props.fit) {
    const fs = await computeFitScale()
    if (fs !== null) {
      scale.value = Math.min(props.initialScale, fs)
    }
  }
  await render()
}

/** 计算「恰好填满 wrap 容器」的 CSS 像素 scale（不含 DPR；render() 内部再乘 DPR）。
 *  返回 null 表示容器尚未布局（clientWidth/clientHeight = 0），调用方应 fallback 到 initialScale。 */
async function computeFitScale(): Promise<number | null> {
  if (!pdfDoc || !wrapRef.value) return null
  const p = await pdfDoc.getPage(page.value)
  const nativeViewport = p.getViewport({ scale: 1 })
  const cw = wrapRef.value.clientWidth
  const ch = wrapRef.value.clientHeight
  if (cw <= 0 || ch <= 0) return null
  const pad = 16  // 留边距；canvas 自带 1px border
  const fitW = (cw - pad) / nativeViewport.width
  const fitH = (ch - pad) / nativeViewport.height
  return Math.min(fitW, fitH)
}

async function render() {
  if (!pdfDoc || !canvasRef.value) return
  if (renderTask) {
    try {
      renderTask.cancel()
    } catch {
      /* ignore */
    }
  }
  const p = await pdfDoc.getPage(page.value)
  const viewport = p.getViewport({ scale: scale.value * (window.devicePixelRatio || 1) })
  const canvas = canvasRef.value
  canvas.width = viewport.width
  canvas.height = viewport.height
  canvas.style.width = `${viewport.width / (window.devicePixelRatio || 1)}px`
  canvas.style.height = `${viewport.height / (window.devicePixelRatio || 1)}px`
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  renderTask = p.render({ canvas: canvas, canvasContext: ctx, viewport })
  await renderTask.promise.catch(() => {
    /* cancelled */
  })
}

function prevPage() {
  if (page.value > 1) {
    page.value -= 1
    void render()
  }
}
function nextPage() {
  if (page.value < totalPages.value) {
    page.value += 1
    void render()
  }
}
function zoomIn() {
  scale.value = Math.min(3, +(scale.value + 0.2).toFixed(1))
  void render()
}
function zoomOut() {
  scale.value = Math.max(0.4, +(scale.value - 0.2).toFixed(1))
  void render()
}
function download() {
  const a = document.createElement('a')
  a.href = props.url
  a.target = '_blank'
  a.rel = 'noopener'
  a.download = ''
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

watch(
  () => props.url,
  () => {
    void load()
  },
)
watch(
  () => props.page,
  (v) => {
    page.value = v
    void render()
  },
)

onMounted(load)
onBeforeUnmount(() => {
  if (renderTask) {
    try {
      renderTask.cancel()
    } catch {
      /* ignore */
    }
  }
  if (pdfDoc) {
    void pdfDoc.cleanup()
  }
})
</script>

<style lang="scss" scoped>
.pdf-viewer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;          // 填满 el-dialog body（全屏时 = 100vh - 头部）
  min-height: 0;         // 让 .canvas-wrap 可以收缩（fit 测量需要）
}
.loading,
.error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px;
  color: var(--text-secondary);
}
.canvas-wrap {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1 1 auto;        // 占据 toolbar 后的剩余高度（fit 测量容器）
  min-height: 0;
  overflow: auto;        // PDF 比 fit 还大时可滚动
}
.pdf-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.page-info {
  font-size: 13px;
  color: var(--text-secondary);
}
.pdf-canvas {
  display: block;
  margin: 0 auto;
  border: 1px solid var(--border-color);
  background: #fff;
  max-width: 100%;
  height: auto;
}
</style>
