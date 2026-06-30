// api/assembly.ts
//
// 装配体 REST 封装。注意创建是 multipart：data 字段为 JSON 字符串，file 字段为 PDF。

import type {
  AssemblyCreatePayload,
  AssemblyCreateResult,
  AssemblyDetail,
  AssemblyListQuery,
  AssemblyListResult,
  AssemblyItem,
} from '@/types/assembly'
import type { DrawingFileItem } from '@/types/file'

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

async function unwrap<T>(resp: Response): Promise<T> {
  const json = (await resp.json()) as ApiEnvelope<T>
  if (json.code !== 0) {
    throw new Error(json.message || `API error code=${json.code}`)
  }
  return json.data
}

function buildQuery(q: AssemblyListQuery = {}): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === '') continue
    qs.set(k, String(v))
  }
  return qs.toString() ? `?${qs.toString()}` : ''
}

export async function listAssemblies(
  q: AssemblyListQuery = {},
): Promise<AssemblyListResult> {
  const resp = await fetch(`/api/v1/assemblies${buildQuery(q)}`)
  return unwrap<AssemblyListResult>(resp)
}

export async function getAssembly(id: number): Promise<AssemblyDetail> {
  const resp = await fetch(`/api/v1/assemblies/${id}`)
  return unwrap<AssemblyDetail>(resp)
}

export async function getAssemblyForPart(
  partId: number,
): Promise<AssemblyDetail> {
  const resp = await fetch(`/api/v1/parts/${partId}/assembly`)
  return unwrap<AssemblyDetail>(resp)
}

export async function createAssembly(
  payload: AssemblyCreatePayload,
  pdfFile: File,
): Promise<AssemblyCreateResult> {
  const form = new FormData()
  form.append('data', JSON.stringify(payload))
  form.append('file', pdfFile)
  const resp = await fetch('/api/v1/assemblies', {
    method: 'POST',
    body: form,
  })
  return unwrap<AssemblyCreateResult>(resp)
}

export async function softDeleteAssembly(id: number): Promise<void> {
  const resp = await fetch(`/api/v1/assemblies/${id}/soft-delete`, {
    method: 'POST',
  })
  await unwrap<{ ok: boolean }>(resp)
}

// ---- 文件相关 ----

export async function listAssemblyFiles(
  id: number,
): Promise<DrawingFileItem[]> {
  const resp = await fetch(`/api/v1/assemblies/${id}/files`)
  return unwrap<DrawingFileItem[]>(resp)
}

export async function uploadAssemblyFile(
  id: number,
  file: File,
): Promise<DrawingFileItem> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`/api/v1/assemblies/${id}/files`, {
    method: 'POST',
    body: form,
  })
  return unwrap<DrawingFileItem>(resp)
}

export async function listPartFiles(partId: number): Promise<DrawingFileItem[]> {
  const resp = await fetch(`/api/v1/parts/${partId}/files`)
  return unwrap<DrawingFileItem[]>(resp)
}

export async function uploadPartFile(
  partId: number,
  file: File,
): Promise<DrawingFileItem> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`/api/v1/parts/${partId}/files`, {
    method: 'POST',
    body: form,
  })
  return unwrap<DrawingFileItem>(resp)
}

export async function deleteFile(fileId: number): Promise<void> {
  const resp = await fetch(`/api/v1/drawings/${fileId}/delete`, {
    method: 'POST',
  })
  await unwrap<{ ok: boolean }>(resp)
}

export async function getDownloadUrl(fileId: number): Promise<string> {
  const resp = await fetch(`/api/v1/drawings/${fileId}/download-url`)
  return unwrap<{ url: string }>(resp).then((d) => d.url)
}

/** 类型守卫 */
export function isAssemblyItem(v: unknown): v is AssemblyItem {
  return !!v && typeof v === 'object' && 'drawing_no' in v && 'child_count' in v
}
