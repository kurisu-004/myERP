// CNC 程序 + 设定单 API 封装（2026-07-10 起统一为 part file）。
// 与后端 api/v1/cnc_program.py 对齐。

import { api } from '@/api/http'
import type { PartFileItem } from '@/types/part_file'

/** 列出某零件已上传的所有 G 代码程序。 */
export async function listPartCncPrograms(
  partId: string,
  kind: 'G_CODE' | 'SETUP_SHEET' = 'G_CODE',
): Promise<PartFileItem[]> {
  const resp = await api.get<PartFileItem[]>(
    `/parts/${partId}/cnc-programs`,
    { params: { kind } },
  )
  return resp.data
}

/** 列出某零件的 CNC 设定单（PDF）。 */
export async function listPartSetupSheets(partId: string): Promise<PartFileItem[]> {
  const resp = await api.get<PartFileItem[]>(`/parts/${partId}/setup-sheets`)
  return resp.data
}

/** 上传 G 代码文件（NC / TAP / CNC / MPF / NGC）。 */
export async function uploadPartCncProgram(
  partId: string,
  file: File,
): Promise<PartFileItem> {
  const fd = new FormData()
  fd.append('file', file)
  const resp = await api.post<PartFileItem>(
    `/parts/${partId}/cnc-programs`,
    fd,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return resp.data
}

/** 上传 CNC 设定单（PDF）。 */
export async function uploadPartSetupSheet(
  partId: string,
  file: File,
): Promise<PartFileItem> {
  const fd = new FormData()
  fd.append('file', file)
  const resp = await api.post<PartFileItem>(
    `/parts/${partId}/setup-sheets`,
    fd,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return resp.data
}

/** 重新签发单文件临时下载 URL（兼容旧 /cnc-programs 前缀）。 */
export async function getCncDownloadUrl(fileId: string): Promise<string> {
  const resp = await api.get<{ url: string }>(`/cnc-programs/${fileId}/download-url`)
  return resp.data.url
}

/** 软删 CNC 文件（COS 对象异步清理；按文件 kind 自动派角色）。 */
export async function deleteCncProgram(fileId: string): Promise<void> {
  await api.post(`/cnc-programs/${fileId}/delete`)
}

/** 配对上传：G 代码 + CNC 设定单 PDF。返回 [gcode, setup_sheet] 两个 PartFileItem。 */
export async function uploadCncPair(
  partId: string,
  gcodeFile: File,
  setupFile: File,
): Promise<[PartFileItem, PartFileItem]> {
  const fd = new FormData()
  fd.append('gcode_file', gcodeFile)
  fd.append('setup_file', setupFile)
  const resp = await api.post<PartFileItem[]>(
    `/parts/${partId}/cnc-pair`,
    fd,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return [resp.data[0], resp.data[1]]
}