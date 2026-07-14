// 装配体 REST API（走 @/api/http 统一 axios 客户端）。
// 创建 / 上传文件走 multipart：data 字段为 JSON 字符串，file 字段为 PDF。

import { api } from '@/api/http'
import type {
  AssemblyCreatePayload,
  AssemblyCreateResult,
  AssemblyDetail,
  AssemblyListQuery,
  AssemblyListResult,
  AssemblyItem,
  AssemblyUpdatePayload,
} from '@/types/assembly'
import type { PartFileItem } from '@/types/part_file'
import type { PartListItem } from '@/types/parts'

function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    out[k] = v
  }
  return out
}

export async function listAssemblies(
  q: AssemblyListQuery = {},
): Promise<AssemblyListResult> {
  const resp = await api.get<AssemblyListResult>('/assemblies', {
    params: cleanParams(q),
  })
  return resp.data
}

export async function getAssembly(id: string): Promise<AssemblyDetail> {
  const resp = await api.get<AssemblyDetail>(`/assemblies/${id}`)
  return resp.data
}

export async function getAssemblyForPart(
  partId: string,
): Promise<AssemblyDetail> {
  const resp = await api.get<AssemblyDetail>(`/parts/${partId}/assembly`)
  return resp.data
}

export async function createAssembly(
  payload: AssemblyCreatePayload,
): Promise<AssemblyCreateResult> {
  const form = new FormData()
  form.append('data', JSON.stringify(payload))
  // 不传 file：创建空装配体；详情页再上传 PDF / 添加子件
  const resp = await api.post<AssemblyCreateResult>('/assemblies', form)
  return resp.data
}

/** 一次性创建：上传总装 PDF + 子件一并生成。 */
export async function createAssemblyWithFile(
  payload: AssemblyCreatePayload,
  pdfFile: File,
): Promise<AssemblyCreateResult> {
  const form = new FormData()
  form.append('data', JSON.stringify(payload))
  form.append('file', pdfFile)
  const resp = await api.post<AssemblyCreateResult>('/assemblies', form)
  return resp.data
}

/** 详情页上传总装 PDF：拆页 → 自动创建子件。 */
export async function uploadAssemblyPdf(
  id: string,
  file: File,
): Promise<AssemblyDetail> {
  const form = new FormData()
  form.append('file', file)
  const resp = await api.post<AssemblyDetail>(`/assemblies/${id}/upload-pdf`, form)
  return resp.data
}

/** 详情页添加单个子件（无 PDF；如需 PDF 走 uploadPartFile）。 */
export async function addAssemblyChild(
  id: string,
  payload: { drawing_no: string; name: string; quantity: number },
): Promise<PartListItem> {
  const resp = await api.post<PartListItem>(`/assemblies/${id}/children`, payload)
  return resp.data
}

export async function softDeleteAssembly(id: string): Promise<void> {
  await api.post(`/assemblies/${id}/soft-delete`)
}

/** 取消装配体（CLERK+）。级联取消所有非终态子件。 */
export async function cancelAssembly(id: string): Promise<AssemblyDetail> {
  const resp = await api.post<AssemblyDetail>(`/assemblies/${id}/cancel`)
  return resp.data
}

/** 编辑装配体元数据（MANAGER + CLERK；仅 PENDING 可编辑）。 */
export async function updateAssembly(
  id: string,
  payload: AssemblyUpdatePayload,
): Promise<AssemblyDetail> {
  const resp = await api.post<AssemblyDetail>(`/assemblies/${id}/update`, payload)
  return resp.data
}

// ---- 文件相关 ----
// 2026-07-10 起：装配体文件由 create_assembly / upload_total_pdf 流创建，
// 不再有独立的 POST /assemblies/{id}/files。list 仍可调用。

export async function listAssemblyFiles(id: string): Promise<PartFileItem[]> {
  const resp = await api.get<PartFileItem[]>(`/assemblies/${id}/files`)
  return resp.data
}

export async function listPartFiles(
  partId: string,
  kind?:
    | 'DRAWING'
    | '3D_MODEL'
    | 'G_CODE'
    | 'SETUP_SHEET'
    | 'ASSEMBLY_MASTER'
    | 'CAD_2D',
): Promise<PartFileItem[]> {
  const resp = await api.get<PartFileItem[]>(`/parts/${partId}/files`, {
    params: kind ? { kind } : {},
  })
  return resp.data
}

/**
 * 上传零件图纸。2026-07-14 起 DRAWING 同时接受 PDF + 8 种图片格式
 * （PNG/JPG/JPEG/GIF/BMP/TIF/TIFF/WEBP/HEIC），后端 /drawings 端点统一处理。
 * 图片与 PDF 同槽（单文件覆盖语义）。
 */
export async function uploadPartDrawing(
  partId: string,
  file: File,
): Promise<PartFileItem> {
  const form = new FormData()
  form.append('file', file)
  const resp = await api.post<PartFileItem>(`/parts/${partId}/drawings`, form)
  return resp.data
}

/** 上传零件 3D 模型（STEP / STP / IGES / IGS / STL / OBJ / 3MF）。 */
export async function uploadPart3DModel(
  partId: string,
  file: File,
): Promise<PartFileItem> {
  const form = new FormData()
  form.append('file', file)
  const resp = await api.post<PartFileItem>(`/parts/${partId}/3d-models`, form)
  return resp.data
}

/**
 * 上传零件 CAD 源文件（DWG / DXF）。2026-07-14 新增 kind=CAD_2D：
 * 与 PDF 图纸生命周期分离，删除 CAD 源不影响打印用 PDF。
 */
export async function uploadPartCadFile(
  partId: string,
  file: File,
): Promise<PartFileItem> {
  const form = new FormData()
  form.append('file', file)
  const resp = await api.post<PartFileItem>(`/parts/${partId}/cad-files`, form)
  return resp.data
}

export async function deleteFile(fileId: string): Promise<void> {
  await api.post(`/files/${fileId}/delete`)
}

export async function getDownloadUrl(fileId: string): Promise<string> {
  const resp = await api.get<{ url: string }>(`/files/${fileId}/download-url`)
  return resp.data.url
}

/** 类型守卫 */
export function isAssemblyItem(v: unknown): v is AssemblyItem {
  return !!v && typeof v === 'object' && 'drawing_no' in v && 'child_count' in v
}