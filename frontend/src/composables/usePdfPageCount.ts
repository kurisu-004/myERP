// composables/usePdfPageCount.ts
//
// 用 pdfjs-dist 在浏览器本地读取 PDF 的页数（不发起网络请求）。
// 用于 AssemblyCreate.vue 上传 PDF 后自动按页生成子零件草稿。
//
// Worker 配置：集中在 @/utils/pdfjs（含 workerSrc 缓存穿透参数）。

import { pdfjsLib } from '@/utils/pdfjs'

/**
 * 读取本地 File 的 PDF 页数。
 * 损坏 / 加密 / 非 PDF → 抛错（调用方负责 ElMessage 提示）。
 */
export async function countPdfPages(file: File): Promise<number> {
  const buf = await file.arrayBuffer()
  const task = pdfjsLib.getDocument({ data: buf })
  try {
    const doc = await task.promise
    return doc.numPages
  } finally {
    // 释放 worker 引用（destroy 在 PDFDocumentLoadingTask 上，不在 proxy 上）
    await task.destroy()
  }
}
