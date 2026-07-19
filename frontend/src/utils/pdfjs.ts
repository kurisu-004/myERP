// utils/pdfjs.ts
//
// pdfjs-dist 单点配置：workerSrc + 浏览器缓存穿透。
// PdfViewer.vue / usePdfPageCount.ts 统一从这里拿 pdfjsLib，不要各自 import 'pdfjs-dist'。
//
// 背景（2026-07-19）：7-17 前生产 nginx 未配 .mjs 的 MIME，pdf worker 以
// application/octet-stream + Cache-Control: max-age=31536000, immutable 下发，
// 被浏览器按年缓存。worker 文件名是 Vite 内容 hash，服务端修复 MIME 后文件名不变，
// 中毒缓存会被整年复用（控制台报「Failed to load module script ... octet-stream」，
// pdfjs 退化到主线程 fake worker）。在 workerSrc 后追加版本查询参数改变缓存键，
// 强制浏览器重新请求拿到修正后的响应。今后若再遇类似缓存中毒，递增下面的版本串即可。

import * as pdfjsLib from 'pdfjs-dist'
import PdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

const PDF_WORKER_CACHE_BUST = 'v=20260719'

pdfjsLib.GlobalWorkerOptions.workerSrc = `${PdfWorkerUrl}?${PDF_WORKER_CACHE_BUST}`

export { pdfjsLib }
