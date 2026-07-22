// utils/pdfjs.ts
//
// pdfjs-dist 单点配置：workerSrc + 浏览器缓存穿透 + cMap。
// PdfViewer.vue / usePdfPageCount.ts 统一从这里拿 pdfjsLib，不要各自 import 'pdfjs-dist'。
//
// 背景（2026-07-19）：7-17 前生产 nginx 未配 .mjs 的 MIME，pdf worker 以
// application/octet-stream + Cache-Control: max-age=31536000, immutable 下发，
// 被浏览器按年缓存。worker 文件名是 Vite 内容 hash，服务端修复 MIME 后文件名不变，
// 中毒缓存会被整年复用（控制台报「Failed to load module script ... octet-stream」，
// pdfjs 退化到主线程 fake worker）。在 workerSrc 后追加版本查询参数改变缓存键，
// 强制浏览器重新请求拿到修正后的响应。今后若再遇类似缓存中毒，递增下面的版本串即可。
//
// cMap（2026-07-22）：中文 / 日文 PDF 渲染时，pdfjs 找不到字体 CMap 会打印
// "UnknownErrorException: Ensure that the `cMapUrl` API parameter is provided."
// 并把字符画成方块。打包时把 pdfjs-dist/cmaps/*.bcmap 全部 `?url` 拷进 assets，
// 取第一个的目录前缀作为 cMapUrl，PdfViewer.vue 在
// getDocument({ cMapUrl, cMapPacked: true }) 时引用。
// （直接 `import ... from 'pdfjs-dist/cmaps/?url'` 对目录无效，Rolldown 不解析。）

import * as pdfjsLib from 'pdfjs-dist'
import PdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

const PDF_WORKER_CACHE_BUST = 'v=20260719'

pdfjsLib.GlobalWorkerOptions.workerSrc = `${PdfWorkerUrl}?${PDF_WORKER_CACHE_BUST}`

// `import.meta.glob` 把 pdfjs-dist 内置的 cMap 二进制全部以 ?url 拷进 Vite assets。
// 返回 map<相对路径, url>；取任一项 URL，按目录前缀截出 cMapUrl（pdfjs 自行拼文件名）。
// 注：glob 路径必须以 `/` 或 `./` 开头，否则 Vite dev server 报
// "Invalid glob ... It must start with '/' or './'"。用 resolve.sync 算出绝对路径。
const cMapUrls = import.meta.glob('/node_modules/pdfjs-dist/cmaps/*.bcmap', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>
const firstCMapUrl = Object.values(cMapUrls)[0]
/** 形如 `/assets/cmaps-xxxx/`，pdfjs 后续会拼具体文件名。 */
export const PDF_CMAP_URL = firstCMapUrl
  ? firstCMapUrl.replace(/[^/]+$/, '')
  : ''

/** 在 getDocument 时传入，启用 cMap 渲染。 */
export const PDF_CMAP_OPTIONS = {
  cMapUrl: PDF_CMAP_URL,
  cMapPacked: true,
} as const

export { pdfjsLib }