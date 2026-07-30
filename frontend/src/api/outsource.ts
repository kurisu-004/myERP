// 外协公司 (OutsourceCompany) API 封装。

import { api } from '@/api/http'
import type {
  ApprovedForSendListResult,
  ApprovedQuoteForSendItem,
  OutsourceCompany,
  OutsourceCompanyCreatePayload,
  OutsourceCompanyListResult,
  OutsourceCompanyUpdatePayload,
  OutsourceCompanyWithProcesses,
  OutsourceInFlightItem,
  OutsourceQuote,
  OutsourceQuoteApprovePayload,
  OutsourceQuoteCreatePayload,
  OutsourceQuoteListResult,
  OutsourceQuoteRejectPayload,
  OutsourceQuoteStatus,
  OutsourceQuoteUpdatePayload,
  OutsourceReconciliationUpdatePayload,
  OutsourceSentPartListResult,
  OutsourceSentPartSortKey,
  SetOutsourceCompanyProcessesPayload,
} from '@/types/outsource'
import type { SortDir } from '@/types/parts'
import type { PartItem, PartListItem } from '@/types/parts'

function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

export async function listOutsourceCompanies(
  params: {
    name_like?: string
    is_active?: boolean
    limit?: number
    offset?: number
  } = {},
): Promise<OutsourceCompanyListResult> {
  const resp = await api.get<OutsourceCompanyListResult>(
    '/outsource-companies',
    { params: cleanParams(params) },
  )
  return resp.data
}

export async function getOutsourceCompany(
  id: string,
): Promise<OutsourceCompanyWithProcesses> {
  const resp = await api.get<OutsourceCompanyWithProcesses>(
    `/outsource-companies/${encodeURIComponent(id)}`,
  )
  return resp.data
}

/** 按工序反查能做此 OUTSOURCE 工序的活跃公司（发送外协对话框用） */
export async function listCompaniesByProcess(
  processId: string,
): Promise<OutsourceCompany[]> {
  const resp = await api.get<OutsourceCompany[]>(
    `/outsource-companies/by-process/${encodeURIComponent(processId)}`,
  )
  return resp.data
}

export async function createOutsourceCompany(
  payload: OutsourceCompanyCreatePayload,
): Promise<OutsourceCompanyWithProcesses> {
  const resp = await api.post<OutsourceCompanyWithProcesses>(
    '/outsource-companies',
    payload,
  )
  return resp.data
}

export async function updateOutsourceCompany(
  id: string,
  payload: OutsourceCompanyUpdatePayload,
): Promise<OutsourceCompanyWithProcesses> {
  const resp = await api.post<OutsourceCompanyWithProcesses>(
    `/outsource-companies/${encodeURIComponent(id)}/update`,
    payload,
  )
  return resp.data
}

export async function softDeleteOutsourceCompany(id: string): Promise<void> {
  await api.post(
    `/outsource-companies/${encodeURIComponent(id)}/soft-delete`,
  )
}

export async function setOutsourceCompanyProcesses(
  id: string,
  payload: SetOutsourceCompanyProcessesPayload,
): Promise<OutsourceCompanyWithProcesses> {
  const resp = await api.post<OutsourceCompanyWithProcesses>(
    `/outsource-companies/${encodeURIComponent(id)}/processes`,
    payload,
  )
  return resp.data
}
// ============================================================
// 外协报价 (OutsourceQuote) API — 2026-07-16 新增
// ============================================================

export async function listOutsourceQuotes(
  params: {
    status?: OutsourceQuoteStatus
    statuses?: OutsourceQuoteStatus[]
    part_id?: string
    outsource_company_id?: string
    customer_id?: string
    keyword?: string
    sort_by?: 'CREATED_AT' | 'PRICE' | 'REVIEWED_AT'
    sort_dir?: 'ASC' | 'DESC'
    limit?: number
    offset?: number
  } = {},
): Promise<OutsourceQuoteListResult> {
  const resp = await api.get<OutsourceQuoteListResult>(
    '/outsource-quotes',
    { params: cleanParams(params) },
  )
  return resp.data
}

export async function getOutsourceQuote(id: string): Promise<OutsourceQuote> {
  const resp = await api.get<OutsourceQuote>(
    `/outsource-quotes/${encodeURIComponent(id)}`,
  )
  return resp.data
}

export async function createOutsourceQuote(
  payload: OutsourceQuoteCreatePayload,
): Promise<OutsourceQuote> {
  const resp = await api.post<OutsourceQuote>('/outsource-quotes', payload)
  return resp.data
}

export async function updateOutsourceQuote(
  id: string,
  payload: OutsourceQuoteUpdatePayload,
): Promise<OutsourceQuote> {
  const resp = await api.post<OutsourceQuote>(
    `/outsource-quotes/${encodeURIComponent(id)}/update`,
    payload,
  )
  return resp.data
}

export async function submitOutsourceQuote(id: string): Promise<OutsourceQuote> {
  const resp = await api.post<OutsourceQuote>(
    `/outsource-quotes/${encodeURIComponent(id)}/submit`,
  )
  return resp.data
}

export async function approveOutsourceQuote(
  id: string,
  payload: OutsourceQuoteApprovePayload,
): Promise<OutsourceQuote> {
  const resp = await api.post<OutsourceQuote>(
    `/outsource-quotes/${encodeURIComponent(id)}/approve`,
    payload,
  )
  return resp.data
}

export async function rejectOutsourceQuote(
  id: string,
  payload: OutsourceQuoteRejectPayload,
): Promise<OutsourceQuote> {
  const resp = await api.post<OutsourceQuote>(
    `/outsource-quotes/${encodeURIComponent(id)}/reject`,
    payload,
  )
  return resp.data
}

export async function softDeleteOutsourceQuote(id: string): Promise<void> {
  await api.post(
    `/outsource-quotes/${encodeURIComponent(id)}/soft-delete`,
  )
}

export async function listApprovedForSend(
  params: {
    keyword?: string
    customer_id?: string
    limit?: number
    offset?: number
  } = {},
): Promise<ApprovedForSendListResult> {
  const resp = await api.get<ApprovedForSendListResult>(
    '/outsource-quotes/approved-for-send',
    { params: cleanParams(params) },
  )
  return resp.data
}

/**
 * 新建报价 picker 默认筛选（PR-H 2026-07-28）：
 * 仅返回「位于绑定了外协工序的货架上」的零件。
 * 返回 PartListItem 列表（包含 next_process_id / next_process_name，用于自动填工序）。
 */
export async function listQuotableParts(
  params: { keyword?: string; limit?: number } = {},
): Promise<PartListItem[]> {
  const resp = await api.get<PartListItem[]>(
    '/outsource-quotes/quotable-parts',
    { params: cleanParams(params) },
  )
  return resp.data
}

/**
 * 外协对账一览（2026-07-28 新增）：列出发送给某外协公司的所有零件 + 当前状态。
 * 用于与外协公司发来的对账单核对。
 */
export async function listCompanySentParts(
  companyId: string,
  params: {
    keyword?: string
    sent_from?: string      // ISO datetime
    sent_to?: string        // ISO datetime
    received_from?: string  // ISO datetime
    received_to?: string    // ISO datetime
    sort_by?: OutsourceSentPartSortKey
    sort_dir?: SortDir
    limit?: number
    offset?: number
  } = {},
): Promise<OutsourceSentPartListResult> {
  const resp = await api.get<OutsourceSentPartListResult>(
    `/outsource-companies/${encodeURIComponent(companyId)}/sent-parts`,
    { params: cleanParams(params) },
  )
  return resp.data
}

/**
 * PR-H 2026-07-30：对账页行编辑（双击单价/数量/对账标记，Enter 确认 / Esc 取消）。
 * POST /outsource-shipments/{shipmentId}/reconcile-update
 */
export async function reconcileUpdateShipment(
  shipmentId: string,
  payload: OutsourceReconciliationUpdatePayload,
): Promise<void> {
  await api.post(
    `/outsource-shipments/${encodeURIComponent(shipmentId)}/reconcile-update`,
    payload,
  )
}

/**
 * 外协中批次列表（2026-07-30 新增）：列出所有已发送但尚未回收的外协批次。
 * GET /parts/outsource-in-flight
 * 注意：后端返回 plain list（无 total），分页 total 取列表长度。
 */
export async function listOutsourceInFlight(
  params: {
    keyword?: string
    limit?: number
    offset?: number
  } = {},
): Promise<OutsourceInFlightItem[]> {
  const resp = await api.get<OutsourceInFlightItem[]>(
    '/parts/outsource-in-flight',
    { params: cleanParams(params) },
  )
  return resp.data
}
