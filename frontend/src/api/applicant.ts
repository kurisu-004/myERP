// 申请人 (Applicant) API 封装。

import { api } from '@/api/http'
import type {
  Applicant,
  ApplicantCreatePayload,
  ApplicantListResult,
  ApplicantSearchParams,
  ApplicantUpdatePayload,
} from '@/types/applicant'

function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

export async function listApplicants(
  params: {
    customer_id?: string
    name_like?: string
    limit?: number
    offset?: number
  } = {},
): Promise<ApplicantListResult> {
  const resp = await api.get<ApplicantListResult>('/applicants', {
    params: cleanParams(params),
  })
  return resp.data
}

/**
 * 申请人前序查询：零件对话框自动补全用。
 * `customer_id` 必须是一级客户的 id（后端会校验 parent_id IS NULL）。
 */
export async function searchApplicants(
  params: ApplicantSearchParams,
): Promise<Applicant[]> {
  const resp = await api.get<Applicant[]>('/applicants/search', {
    params: cleanParams(params),
  })
  return resp.data
}

export async function getApplicant(id: string): Promise<Applicant> {
  const resp = await api.get<Applicant>(`/applicants/${id}`)
  return resp.data
}

export async function createApplicant(
  payload: ApplicantCreatePayload,
): Promise<Applicant> {
  const resp = await api.post<Applicant>('/applicants', payload)
  return resp.data
}

export async function updateApplicant(
  id: string,
  payload: ApplicantUpdatePayload,
): Promise<Applicant> {
  const resp = await api.post<Applicant>(`/applicants/${id}/update`, payload)
  return resp.data
}

export async function softDeleteApplicant(id: string): Promise<void> {
  await api.post(`/applicants/${id}/soft-delete`)
}