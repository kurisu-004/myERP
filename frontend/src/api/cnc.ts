// 后端 CNC 程序（G 代码）API 封装。
// 与后端 api/v1/cnc_program.py 端点对齐。

import { api } from '@/api/http'
import type { CncProgramItem } from '@/types/cnc'

/** 列出某零件已上传的所有 G 代码程序。 */
export async function listPartCncPrograms(partId: string): Promise<CncProgramItem[]> {
  const resp = await api.get<CncProgramItem[]>(`/parts/${partId}/cnc-programs`)
  return resp.data
}

/** 上传单个 G 代码文件。 */
export async function uploadPartCncProgram(
  partId: string,
  file: File,
): Promise<CncProgramItem> {
  const fd = new FormData()
  fd.append('file', file)
  const resp = await api.post<CncProgramItem>(
    `/parts/${partId}/cnc-programs`,
    fd,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return resp.data
}

/** 重新签发单文件临时下载 URL。 */
export async function getCncDownloadUrl(fileId: string): Promise<string> {
  const resp = await api.get<{ url: string }>(`/cnc-programs/${fileId}/download-url`)
  return resp.data.url
}

/** 软删 G 代码程序（COS 对象异步清理）。 */
export async function deleteCncProgram(fileId: string): Promise<void> {
  await api.post(`/cnc-programs/${fileId}/delete`)
}
