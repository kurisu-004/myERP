/**
 * 浏览器端触发文件下载的工具（2026-08-02 新增；从 DeliveryNoteDetail.vue 抽出）。
 *
 * 用 ``Blob`` + ``URL.createObjectURL`` + 临时 ``<a download>`` 触发浏览器保存。
 * 调用方负责传入后端响应里 ``Content-Disposition`` 解析出的 ``filename``（含扩展名）。
 */
export function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } finally {
    // 延后 revoke，给浏览器一点时间发起下载
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}