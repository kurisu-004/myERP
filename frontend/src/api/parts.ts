// 后端零件 API 封装（走 @/api/http 统一 axios 客户端）。
// 所有 ID 在前端是字符串（雪花 ID 经后端 IdStr 序列化）。

import { api } from '@/api/http'
import type { PartFileItem } from '@/types/part_file'
import type {
  DirectOutsourceCandidateListResult,
} from '@/types/directOutsource'
import type {
  OutsourceSendableListResult,
} from '@/types/outsource'
import type {
  OrderStatus,
  PartEventType,
  PartListItem,
  PartSortKey,
  SortDir,
} from '@/types/parts'

export interface PartItem {
  id: string
  serial_no: string | null
  name: string
  drawing_no: string
  quantity: number
  planned_delivery_date: string
  actual_delivery_date: string | null
  is_urgent: boolean
  status: OrderStatus
  /** PR-F 2026-07-17：送货单字段 */
  order_no: string | null
  system_delivery_date: string | null
  note: string | null
  customer_name: string | null
  parent_customer_name: string | null
  customer_path: string | null
  /** PR-G 2026-07-22：所属送货单（NULL = 未开单；详情页可链接到送货单） */
  delivery_note_id: string | null
  /** PR-G 2026-07-22：所属送货单单号 DN-YYYYMMDD-NNNN（与 detail.batch_fetch 同事务返回） */
  delivery_note_no: string | null
  /** PR-G 2026-07-22：所属送货单状态 */
  delivery_note_status: string | null
  assembly_id: string | null
  current_holder_id: string | null
  current_holder_kind: 'shelf' | 'worker' | 'outsource_company' | null
  shelf_code: string | null
  worker_name: string | null
  /** holder 是外协公司时的公司名（2026-07-15 接入） */
  outsource_company_name: string | null
  location: string | null
  placed_at: string | null
  /** 下一道工序 id（NULL = 未设置） */
  next_process_id: string | null
  /** 下一道工序名称（NULL = 未设置；由后端在 list/get 响应中带出） */
  next_process_name: string | null
  /**
   * 2026-07-21：该 part 最近一次品检打回（INSPECTION_FAILED）事件的 note。
   * 格式：`"打回到货架：<code> 下一工序：<code> | 备注：<note>"`。
   * 仅 PICK_UP 扫码列表返回（后端 list_for_work_type* 走 LEFT JOIN LATERAL 计算），
   * 其它端点为 null。
   */
  last_inspection_fail_note?: string | null
  /** 2026-07-29 批次化：批次级列表（扫码台/品检待办）填充；quantity 为批次量 */
  batch_id?: string | null
  batch_no?: number | null
  batch_label?: string | null
}

export interface PartListResult {
  items: PartListItem[]
  total: number
  limit: number
  offset: number
}

/** 雪花 ID 字符串（CLAUDE.md §3 — 19 位 > JS Number.MAX_SAFE_INTEGER） */
export interface ListPartsParams {
  customer_id?: string
  statuses?: OrderStatus[]
  is_urgent?: boolean
  keyword?: string
  /** 2026-07-22：订单号独立搜索（ILIKE 包含 %kw%）。 */
  order_no?: string
  /**
   * 仅返回「曾外协过」的零件（2026-07-20 新增，外协接收历史页用）。
   * 命中条件由后端 EXISTS 子查询判定（SENT_TO_OUTSOURCE / RECEIVED_FROM_OUTSOURCE
   * / INSPECTED + note ILIKE '%外协%'）。
   */
  has_outsource_history?: boolean
  /** 2026-07-21 PR-F：请购日期区间（含端点；任一端点为空表示半开） */
  request_date_from?: string
  request_date_to?: string
  /** 2026-07-22：计划交期区间（含端点；任一端点为空表示半开） */
  planned_delivery_date_from?: string
  planned_delivery_date_to?: string
  /** 2026-07-21 PR-F：系统交期区间（含端点；任一端点为空表示半开；NULL 字段视为落在区间内） */
  system_delivery_date_from?: string
  system_delivery_date_to?: string
  sort_by?: PartSortKey
  sort_dir?: SortDir
  limit?: number
  offset?: number
  /** 2026-07-30：零件一览合并装配件 */
  include_assemblies?: boolean
}

export interface PartCreatePayload {
  name: string
  drawing_no: string
  applicant_name?: string
  /**
   * 申请人表 id（雪花 ID 字符串）。
   * 必须是字符串：雪花 ID 19 位 > JS Number.MAX_SAFE_INTEGER（2^53-1），
   * 用 number 类型会在 JSON 序列化时丢精度，后端拿不到原值。
   */
  applicant_id?: string | null
  quantity?: number
  unit_price?: number
  total_price?: number | null
  request_date: string
  planned_delivery_date: string
  actual_delivery_date?: string | null
  is_urgent?: boolean
  /** PR-F 2026-07-17：送货单字段 */
  order_no?: string | null
  system_delivery_date?: string | null
  note?: string | null
  /** 雪花 ID 字符串（CLAUDE.md §3） */
  customer_id: string
}

export interface PartStatusChangePayload {
  status: OrderStatus
}

export interface PartUpdatePayload {
  name?: string
  drawing_no?: string
  applicant_name?: string
  quantity?: number
  unit_price?: number
  total_price?: number | null
  request_date?: string
  planned_delivery_date?: string
  actual_delivery_date?: string | null
  is_urgent?: boolean
  /** PR-F 2026-07-17：送货单字段 */
  order_no?: string | null
  system_delivery_date?: string | null
  note?: string | null
  /** 雪花 ID 字符串（CLAUDE.md §3） */
  customer_id?: string
}

export interface PartPickUpPayload {
  serial_no: string
  shelf_id: string
  badge_code: string
  /** 2026-07-29 批次化：目标批次 id（扫码台卡片回传） */
  batch_id?: string | null
  /** 领取数量；缺省 = 批次全量 */
  quantity?: number | null
}

export interface PartScanPayload {
  serial_no: string
  event_type: PartEventType
  shelf_id: string
  badge_code: string
  target_inspection_shelf_id?: string | null
  /** 仅 RETURNED 需要；工人指定的下一道工序 id */
  next_process_id?: string | null
  /** 2026-07-29 批次化：目标批次 id（扫码台卡片回传） */
  batch_id?: string | null
  /** 归还/送检数量；缺省 = 批次全量 */
  quantity?: number | null
}

export interface PartEvent {
  id: string
  part_id: string
  /** 2026-07-29 批次化：事件归属批次（NULL = 工单级事件） */
  batch_id: string | null
  batch_no: number | null
  /** 本次事件涉及的数量 */
  quantity: number | null
  worker_id: string | null
  worker_name: string | null
  event_type: string
  from_status: string | null
  to_status: string | null
  drawing_code: string | null
  badge_code: string | null
  note: string | null
  created_by: string | null
  operator_username: string | null
  // 2026-07-17：操作者姓名（display_name）；前端 UI 默认用它，username 仅作 fallback
  operator_name: string | null
  created_at: string
}

export interface PartBatchFailure {
  index: number
  message: string
}

export interface PartBatchResult {
  created: PartItem[]
  failed: PartBatchFailure[]
}

// axios 会自动丢掉 undefined/null；但空串不会丢（会触发 LIKE '%%'）。
// 这里显式 filter 一下，确保空字符串参数也跳过。
function cleanParams<T extends object>(p: T): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

export async function listParts(
  params: ListPartsParams = {},
): Promise<PartListResult> {
  const resp = await api.get<PartListResult>('/parts', { params: cleanParams(params) })
  return resp.data
}

export async function getPart(id: string): Promise<PartItem> {
  const resp = await api.get<PartItem>(`/parts/${id}`)
  return resp.data
}

export async function createPart(payload: PartCreatePayload): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts', payload)
  return resp.data
}

export async function changePartStatus(
  id: string,
  payload: PartStatusChangePayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/change-status`, payload)
  return resp.data
}

export interface PlaceOnShelfPayload {
  shelf_id: string
  /** 下一道工序 id（必填） */
  next_process_id: string
}

export async function placeOnShelf(
  id: number | string,
  shelfId: string,
  nextProcessId: string,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/place-on-shelf`, {
    shelf_id: shelfId,
    next_process_id: nextProcessId,
  })
  return resp.data
}

/** PENDING → PROGRAMMING：文员把零件发送至 CNC 编程。 */
export async function sendToProgramming(id: number | string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/send-to-programming`)
  return resp.data
}

/** PROGRAMMING → IN_PROCESS：编程员上传完 G 代码后下发到生产货架。 */
export async function releaseFromProgramming(
  id: number | string,
  shelfId: string,
  nextProcessId: string,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${id}/release-from-programming`,
    { shelf_id: shelfId, next_process_id: nextProcessId },
  )
  return resp.data
}

/** 待编程一览：status=PROGRAMMING 的零件列表。 */
export async function listPendingProgramming(
  params: Omit<ListPartsParams, 'statuses' | 'is_urgent'> = {},
): Promise<PartListResult> {
  const resp = await api.get<PartListResult>(
    '/parts/pending-programming',
    { params: cleanParams(params) },
  )
  return resp.data
}

export async function pickUpPart(payload: PartPickUpPayload): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts/pick-up', payload)
  return resp.data
}

export async function scanPart(payload: PartScanPayload): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts/scan', payload)
  return resp.data
}

export async function listPartEvents(id: string): Promise<PartEvent[]> {
  const resp = await api.get<PartEvent[]>(`/parts/${id}/events`)
  return resp.data
}

export interface PartBatchFilePayload {
  /** 浏览器里的 File 对象（el-upload 的 uploadFile.raw）。 */
  data: Blob
  /** 原始文件名（含扩展名，后端据此判 PDF 类型）。 */
  filename: string
  /** 可选 content-type；后端会按文件扩展名兜底。 */
  contentType?: string
}

/**
 * 批量新建零件（multipart/form-data）。
 *
 * 后端 `POST /parts/batch` 2026-07-09 起接受 `data` (JSON 字符串) + `files` (PDF 数组)，
 * 文件与 items 按下标对齐；缺失位按无图处理。任一上传失败 → 整批回滚。
 *
 * 不手动设 Content-Type —— axios 会自动加正确的 multipart boundary。
 */
export async function batchCreateParts(
  items: PartCreatePayload[],
  files: (PartBatchFilePayload | null)[] = [],
): Promise<PartBatchResult> {
  const form = new FormData()
  form.append('data', JSON.stringify({ items }))
  files.forEach((f) => {
    if (f) form.append('files', f.data, f.filename)
  })
  const resp = await api.post<PartBatchResult>('/parts/batch', form)
  return resp.data
}

// ===== 2026-07-21：批量树形创建（PDF 批量上传，单页=独立零件，多页=装配件+子件） =====

export interface PartBatchTreeAssemblyFE {
  uid: string
  drawing_no: string | null
  name: string | null
  applicant_name: string | null
  applicant_id: string | null
  customer_id: string
  request_date: string
  planned_delivery_date: string
  system_delivery_date?: string | null
  order_no?: string | null
  note?: string | null
  is_urgent: boolean
}

export interface PartBatchTreeItemFE {
  pdf_index: number
  page_index: number
  assembly_uid: string | null
  is_master: boolean
  drawing_no: string
  name: string
  applicant_name: string | null
  applicant_id: string | null
  quantity: number
  customer_id: string
  request_date: string
  planned_delivery_date: string
  system_delivery_date?: string | null
  order_no?: string | null
  note?: string | null
  is_urgent: boolean
  /** PR-H 2026-07-28：含税单价（来自历史价确认单 G 列；可空） */
  unit_price?: number | null
  /** PR-H 2026-07-28：含税价格（来自历史价确认单 I 列；空时按 unit_price × quantity 计算） */
  total_price?: number | null
  /** PR-H 2026-07-28：3D 模型下标（指向 three_d_models 数组；null = 不挂） */
  three_d_index?: number | null
}

export interface PartBatchTreePartResultFE {
  uid: string
  kind: 'part' | 'assembly_child'
  part: PartItem
}

export interface PartBatchTreeAssemblyResultFE {
  uid: string
  assembly: {
    id: string
    serial_no: string | null
    drawing_no: string
    name: string
    status: string
    child_count: number
  }
  master_file: PartFileItem | null
  children: PartBatchTreePartResultFE[]
  child_files: PartFileItem[]
}

export interface PartBatchTreeResultFE {
  standalone_parts: PartBatchTreePartResultFE[]
  assemblies: PartBatchTreeAssemblyResultFE[]
  failed: PartBatchFailure[]
}

/**
 * 批量树形创建：单页 PDF → 独立零件；多页 PDF → 装配件 + 子件。
 * 文件按 `pdf_index` 隐式对齐 `items`（frontend 端按上传顺序记录）。
 * PR-H 2026-07-28：`threeDModels` 按 `items[i].three_d_index` 对齐。
 */
export async function batchCreatePartsWithPdfs(
  items: PartBatchTreeItemFE[],
  assemblies: PartBatchTreeAssemblyFE[],
  files: PartBatchFilePayload[],
  threeDModels: PartBatchFilePayload[] = [],
): Promise<PartBatchTreeResultFE> {
  const form = new FormData()
  form.append('data', JSON.stringify({ items, assemblies }))
  files.forEach((f) => {
    if (f.data) form.append('files', f.data, f.filename)
  })
  threeDModels.forEach((f) => {
    if (f.data) form.append('three_d_models', f.data, f.filename)
  })
  // 批量上传可能耗时数分钟，单点延长到 10 分钟；全局 axios `timeout: 30_000` 不动（其他业务保持短超时）。
  const resp = await api.post<PartBatchTreeResultFE>(
    '/parts/batch-with-pdfs',
    form,
    { timeout: 10 * 60 * 1000 },
  )
  return resp.data
}

export async function softDeletePart(id: string): Promise<void> {
  await api.post(`/parts/${id}/soft-delete`)
}

export async function updatePart(
  id: string,
  payload: PartUpdatePayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/update`, payload)
  return resp.data
}

/** INSPECTION → READY_TO_SHIP：品检合格（2026-07-29：可选批次/部分数量）。 */
export async function passInspection(
  id: string,
  payload?: BatchActionPayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${id}/pass-inspection`,
    payload ?? undefined,
  )
  return resp.data
}

/**
 * 2026-07-21 改：品检打回（INSPECTION → IN_PROCESS）—— 三参 payload：
 * shelf_id + next_process_id（保留为下一道工序，不再清空）+ note（品检备注）。
 * 后端会校验 `t_shelf_process` 映射（缺映射返回 422）。
 */
export interface FailInspectionPayload {
  shelf_id: string
  /** 下一道工序 id（必填；保留为该 part 的下道工序，工人可直接领取） */
  next_process_id: string
  /** 品检员填的不合格原因等（写入 t_part_event.note，事件历史一览可见） */
  note?: string | null
  /** 2026-07-29：目标批次 id；缺省取唯一 INSPECTION 批次 */
  batch_id?: string | null
  /** 2026-07-29：部分数量；缺省 = 批次全量 */
  quantity?: number | null
}

export async function failInspection(
  id: string,
  payload: FailInspectionPayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${id}/fail-inspection`,
    payload,
  )
  return resp.data
}

/** READY_TO_SHIP → DELIVERED：发货（文员/管理员手动）。 */
export async function deliverPart(id: string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/deliver`)
  return resp.data
}

/** 扫码台：司机确认发货（PR-C 2026-07-10）。 */
export interface ScanDeliverPartPayload {
  part_id: string
  worker_badge_code: string
}

export async function scanDeliverPart(
  payload: ScanDeliverPartPayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>('/parts/scan/deliver-part', payload)
  return resp.data
}

/** DELIVERED → COMPLETED：确认完成，释放流水号。 */
export async function completePart(id: string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/complete`)
  return resp.data
}

/** → REPAIRING：开始返修（INSPECTION/READY_TO_SHIP/DELIVERED 进入）。 */
export async function startPartRepair(id: string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/start-repair`)
  return resp.data
}

/** REPAIRING → IN_PROCESS：返修完成，需要指定目标生产货架。 */
export async function completePartRepair(
  id: string,
  shelfId: string,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${id}/complete-repair`,
    null,
    { params: { shelf_id: shelfId } },
  )
  return resp.data
}

export async function cancelPart(id: string): Promise<PartItem> {
  const resp = await api.post<PartItem>(`/parts/${id}/cancel`)
  return resp.data
}

export async function getPartBySerial(serialNo: string): Promise<PartItem> {
  const resp = await api.get<PartItem>(
    `/parts/by-serial/${encodeURIComponent(serialNo)}`,
  )
  return resp.data
}

/**
 * 扫码台 PICK_UP 列表：列出指定工种在指定货架上可领的零件。
 * 排序：加急优先 → 临期优先 → id 降序。
 */
export async function listPartsByWorkType(
  workTypeId: string,
  shelfId: string,
): Promise<PartItem[]> {
  const resp = await api.get<PartItem[]>(
    `/parts/by-work-type/${encodeURIComponent(workTypeId)}`,
    { params: { shelf_id: shelfId } },
  )
  return resp.data
}

/**
 * 共享 HMI PICK_UP 跨架列表（2026-07-10）。
 * 列出**所有**生产货架上、该工种可领的零件；前端按 `current_holder_id`
 * 在卡片网格里分组。
 * 排序与 `listPartsByWorkType` 一致。
 */
export async function listPartsByWorkTypeAllShelves(
  workTypeId: string,
): Promise<PartItem[]> {
  const resp = await api.get<PartItem[]>(
    `/parts/pickable-by-work-type/${encodeURIComponent(workTypeId)}`,
  )
  return resp.data
}

/**
 * 扫码台 RETURN 列表：列出某工人当前持有的所有零件（2026-07-10 新流程）。
 * 排序：加急优先 → 临期优先 → id 降序。
 * 入参 workerId 是雪花 ID 字符串。
 */
export async function listPartsHeldByWorker(
  workerId: string,
): Promise<PartItem[]> {
  const resp = await api.get<PartItem[]>(
    `/parts/by-worker/${encodeURIComponent(workerId)}`,
  )
  return resp.data
}

/**
 * 生成零件的双面打印 PDF（图纸 + 反面右下角条形码）。
 * 返回 Blob，content-type=application/pdf。
 *
 * 注：返回的是文件 blob，调用方需自行用 iframe / window 触发打印。
 */
export async function printPartDrawing(partId: string): Promise<Blob> {
  const resp = await api.get<Blob>(
    `/parts/${encodeURIComponent(partId)}/print-drawing`,
    { responseType: 'blob' },
  )
  return resp.data
}

/**
 * 批量生成多个零件的双面打印 PDF 并合并为一个 PDF（2026-07-17 接入）。
 * 后端把 N 个 part 的双面 PDF 用 pypdf.PdfWriter 顺序拼接成单文件返回。
 * 前端拿到 Blob 后用单 iframe 一次 print()，避免 N 次打印弹窗。
 */
export async function printPartDrawingBatch(
  partIds: string[],
  assemblyIds?: string[],
): Promise<Blob> {
  const resp = await api.post<Blob>(
    '/parts/print-drawing-batch',
    { part_ids: partIds, assembly_ids: assemblyIds },
    { responseType: 'blob', timeout: 10 * 60 * 1000 },
  )
  return resp.data
}

// ============================================================
// 外协流程（2026-07-15 新增）
// ============================================================
export interface SendToOutsourcePayload {
  /** 外协公司 id（雪花 ID 字符串） */
  outsource_company_id: string
  /** 外协工序 id（雪花 ID 字符串；JS Number 会丢精度） */
  next_process_id: string
  /**
   * 乐观锁版本号；与目标批次 TPartBatch.version 必须一致，否则返 BIZ_VERSION_CONFLICT 409。
   * 前端从 OutsourceSendableItem.version（批次级 version）取值后传入。
   * 2026-07-28 新增。
   * 2026-07-29 PR-fix-0.2.0 批次化：改为批次 version。
   */
  version: number
  /**
   * 2026-07-29 PR-fix-0.2.0 批次化：可发送批次 id（雪花 ID 字符串）。
   * 选填 —— 缺省时后端用 _resolve_target_batch 在该 part 的活跃批次里自动选唯一者；
   * 多批次工单建议显式传入，避免歧义。Picker 选中行时建议把 row.batch_id 一起回传。
   */
  batch_id?: string
  /**
   * 2026-07-30：部分发送数量；≤ 批次量，缺省 = 批次全量。
   */
  quantity?: number | null
}

/**
 * PENDING / IN_PROCESS → OUTSOURCE：把零件发送给外协公司。
 * 后端会校验公司存在 + 启用 + 工序 OUTSOURCE + 公司映射了该工序。
 */
export async function sendToOutsource(
  partId: string,
  payload: SendToOutsourcePayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${encodeURIComponent(partId)}/send-to-outsource`,
    payload,
  )
  return resp.data
}

/**
 * 统一外协可发送一览（2026-07-28 新增；取代 listDirectOutsourceCandidates / listApprovedForSend）：
 * 合并 APPROVAL（需审批 + 有报价）和 DIRECT（无需审批可直发）两类，
 * 每行带 send_mode + source_status 字段。
 */
export async function listOutsourceSendable(
  params: {
    keyword?: string
    customer_id?: string
    limit?: number
    offset?: number
  } = {},
): Promise<OutsourceSendableListResult> {
  const resp = await api.get<OutsourceSendableListResult>(
    '/parts/outsource-sendable',
    { params: cleanParams(params) },
  )
  return resp.data
}

/**
 * 直接发送外协候选（已弃用；2026-07-28 后由 listOutsourceSendable 取代）。
 * 保留以兼容旧调用方；新代码请用 listOutsourceSendable。
 */
export async function listDirectOutsourceCandidates(
  params: {
    keyword?: string
    customer_id?: string
    limit?: number
    offset?: number
  } = {},
): Promise<DirectOutsourceCandidateListResult> {
  const resp = await api.get<DirectOutsourceCandidateListResult>(
    '/parts/direct-outsource-candidates',
    { params: cleanParams(params) },
  )
  return resp.data
}

export interface ReceiveFromOutsourcePayload {
  shelf_id: string
  /** 下一道工序 id（雪花 ID 字符串；JS Number 会丢精度） */
  next_process_id: string
  /** 2026-07-30：目标批次 id；缺省按状态唯一批次解析 */
  batch_id?: string | null
  /** 2026-07-30：部分接收数量；缺省 = 批次全量 */
  quantity?: number | null
}

export interface ReceiveToInspectionPayload {
  shelf_id: string
  /** True: 自动通过品检 → READY_TO_SHIP（"送货流程"快捷分支，2026-07-16 加） */
  auto_pass_inspection?: boolean
  /** 2026-07-30：目标批次 id；缺省按状态唯一批次解析 */
  batch_id?: string | null
  /** 2026-07-30：部分接收数量；缺省 = 批次全量 */
  quantity?: number | null
}

/**
 * OUTSOURCE → IN_PROCESS：从外协回收，下发到生产货架继续加工。
 */
export async function receiveFromOutsource(
  partId: string,
  payload: ReceiveFromOutsourcePayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${encodeURIComponent(partId)}/receive-from-outsource`,
    payload,
  )
  return resp.data
}
/**
 * 2026-07-16：OUTSOURCE → INSPECTION：外协件直接送检（跳过生产货架）。
 * auto_pass_inspection=True 时连发 pass_inspection 一次推到 READY_TO_SHIP
 * （"送货流程" 快捷分支 = OUTSOURCE → INSPECTION → READY_TO_SHIP）。
 */
export async function receiveFromOutsourceToInspection(
  partId: string,
  payload: ReceiveToInspectionPayload,
): Promise<PartItem> {
  const resp = await api.post<PartItem>(
    `/parts/${encodeURIComponent(partId)}/receive-from-outsource-to-inspection`,
    payload,
  )
  return resp.data
}

// ============================================================
// 批次（2026-07-29 批次化）
// ============================================================

/** 批次监控条目（详情页批次卡片） */
export interface PartBatch {
  id: string
  version: number
  part_id: string
  batch_no: number
  batch_label: string
  quantity: number
  status: string
  location: string | null
  current_holder_id: string | null
  current_holder_display: string | null
  next_process_id: string | null
  next_process_name: string | null
  placed_at: string | null
  delivery_note_id: string | null
  delivery_note_no: string | null
  parent_batch_id: string | null
  created_at: string
  updated_at: string
}

/** 无 body 流转端点的可选批次参数 */
export interface BatchActionPayload {
  batch_id?: string | null
  quantity?: number | null
}

export async function listPartBatches(partId: string): Promise<PartBatch[]> {
  const resp = await api.get<PartBatch[]>(`/parts/${partId}/batches`)
  return resp.data
}

export async function splitPartBatch(
  partId: string,
  payload: { batch_id: string; quantity: number },
): Promise<PartBatch[]> {
  const resp = await api.post<PartBatch[]>(
    `/parts/${partId}/batches/split`,
    payload,
  )
  return resp.data
}

export async function cancelPartBatch(
  partId: string,
  batchId: string,
): Promise<PartBatch[]> {
  const resp = await api.post<PartBatch[]>(
    `/parts/${partId}/batches/${batchId}/cancel`,
  )
  return resp.data
}

/** 品检待办（批次级；行=批次） */
export interface InspectionBatchListResult {
  items: PartItem[]
  total: number
  limit: number
  offset: number
}

export async function listInspectionBatches(params: {
  keyword?: string
  customer_id?: string
  limit?: number
  offset?: number
} = {}): Promise<InspectionBatchListResult> {
  const resp = await api.get<InspectionBatchListResult>('/parts/inspection-batches', {
    params: cleanParams(params),
  })
  return resp.data
}
