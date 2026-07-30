from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status as http_status
from pydantic import BaseModel, Field

from api.deps import (
    get_applicant_service,
    get_assembly_repo,
    get_outsource_quote_service,
    get_part_file_service,
    get_part_file_repository,
    get_part_repository,
    get_part_service,
)
from core.error_code import ErrCode
from core.exception import BizError
from core.permission import (
    CurrentUser,
    get_current_user,
    require_auth,
    require_role,
    require_roles,
    require_shelf_account_from_body,
)
from model.enums import PartEventType, UserRole
from repository.assembly import AssemblyRepository
from repository.part import PartRepository
from repository.part_file import PartFileRepository
from schema.outsource_quote import OutsourceInFlightItem
from schema.part import (
    BatchSplitRequest,
    InspectionBatchListOut,
    DirectOutsourceCandidateListOut,
    FailInspectionRequest,
    OutsourceSendableListOut,
    PartBatchActionRequest,
    PartBatchCreateRequest,
    PartBatchCreateResult,
    PartBatchOut,
    PartBatchTreeRequest,
    PartBatchTreeResult,
    PartCreateRequest,
    PartEventOut,
    PartListOut,
    PartListQuery,
    PartOut,
    PartPickUpRequest,
    PartScanRequest,
    PartUpdateRequest,
    PlaceOnShelfRequest,
    ReceiveToInspectionRequest,
    SendToOutsourceRequest,
)
from service import PartService, OutsourceQuoteService
from service._id_parse import parse_snowflake_id
from service.applicant import ApplicantService
from service.part_file import PartFileService
from service.printing import build_part_print_pdf, build_parts_print_pdf_batch
from core.time import now_naive

router = APIRouter(prefix="/parts", tags=["零件管理"])


# ============================================================
# MANAGER-only 路由
# ============================================================
_mgr_dep = [Depends(require_role(UserRole.MANAGER))]

# MANAGER + CLERK：文员能下单/查看/编辑/下发/发送CNC编程/取消等前台操作。
# 用户管理、货架管理、工种-工序配置仍保持 MANAGER-only。
_office_dep = [
    Depends(require_roles(UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER))
]

# MANAGER + INSPECTOR：品检相关端点（pass-inspection / fail-inspection）。
# 品检员可以独立验收，不依赖文员（2026-07-23 移除 CLERK：CLERK 不再有品检权限）。
_inspector_dep = [
    Depends(require_roles(
        UserRole.MANAGER, UserRole.INSPECTOR,
    ))
]

# MANAGER + CLERK + INSPECTOR：外协发送 / 接收 端点。
# 品检员扫码批量发送外协 + 接收外协（PR-I 2026-07-20）。
_inspector_outsource_dep = [
    Depends(require_roles(
        UserRole.MANAGER, UserRole.CLERK, UserRole.INSPECTOR,
    ))
]

# MANAGER + CLERK + CNC_PROGRAMMER + INSPECTOR：零件只读端点（列表 / 详情 / 事件）。
# INSPECTOR 只读零件一览 / 详情 / 事件流（PR-I 2026-07-20）。
_read_part_dep = [
    Depends(require_roles(
        UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        UserRole.INSPECTOR,
    ))
]

# MANAGER + CLERK + CNC_PROGRAMMER：待编程一览（INSPECTOR 不需要，仍保持窄权限）
_read_lots_dep = [
    Depends(require_roles(
        UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
    ))
]


@router.get(
    "",
    response_model=PartListOut,
    summary="分页查询零件列表（MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR）",
    dependencies=_read_part_dep,
)
async def list_parts(
    customer_id: str | None = Query(default=None, description="客户 id（雪花 ID 字符串）"),
    statuses: list[str] | None = Query(default=None, description="订单状态多选"),
    is_urgent: bool | None = Query(default=None, description="是否加急"),
    keyword: str | None = Query(
        default=None,
        description="搜索关键字（图号 ILIKE 包含 %kw%；名称 ILIKE 前缀 kw%）",
    ),
    order_no: str | None = Query(
        default=None, description="订单号搜索（ILIKE 包含 %kw%；2026-07-22 新增）"
    ),
    has_outsource_history: bool | None = Query(
        default=None,
        description=(
            "仅返回「曾外协过」的零件（外协接收历史页用，"
            "2026-07-20 新增；按下发/接收/外协相关事件 EXISTS 判定）"
        ),
    ),
    request_date_from: date | None = Query(default=None, description="请购日期区间起点（含）"),
    request_date_to: date | None = Query(default=None, description="请购日期区间终点（含）"),
    planned_delivery_date_from: date | None = Query(default=None, description="计划交期区间起点（含）"),
    planned_delivery_date_to: date | None = Query(default=None, description="计划交期区间终点（含）"),
    system_delivery_date_from: date | None = Query(default=None, description="系统交期区间起点（含）"),
    system_delivery_date_to: date | None = Query(default=None, description="系统交期区间终点（含）"),
    include_assemblies: bool = Query(
        default=False, description="是否合并返回装配件行（子件从顶层隐藏；2026-07-30 新增）"
    ),
    sort_by: str = Query(default="PLANNED_DELIVERY_DATE", description="排序字段"),
    sort_dir: str = Query(default="ASC", description="排序方向"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: PartService = Depends(get_part_service),
) -> PartListOut:
    from model.enums import PartSortKey, PartStatus, SortDir

    return await svc.list_parts(
        PartListQuery(
            customer_id=customer_id,
            statuses=[PartStatus(s) for s in statuses] if statuses else None,
            is_urgent=is_urgent,
            keyword=keyword,
            order_no=order_no,
            has_outsource_history=has_outsource_history,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            include_assemblies=include_assemblies,
            sort_by=PartSortKey(sort_by),
            sort_dir=SortDir(sort_dir),
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=PartOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增 PENDING 零件（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def create_part(
    payload: PartCreateRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.create_part(payload)


@router.post(
    "/batch",
    response_model=PartBatchCreateResult,
    summary="批量新增零件（MANAGER / CLERK；可选随每行上传 PDF 图纸）",
    description=(
        "multipart/form-data：`data` 是 PartBatchCreateRequest 的 JSON 字符串；"
        "`files` 是可选的 PDF 数组，与 items 按下标对齐，"
        "未上传的行用省略或留空表示。任一上传失败 → 整批回滚。"
    ),
    dependencies=_office_dep,
)
async def create_parts_batch(
    data: str = Form(..., description="PartBatchCreateRequest 的 JSON 字符串"),
    files: list[UploadFile] | None = File(
        default=None,
        description=(
            "可选 PDF 数组，与 items 按下标对齐；可少于 items 长度（缺位按无图处理）。"
        ),
    ),
    svc: PartService = Depends(get_part_service),
) -> PartBatchCreateResult:
    payload = PartBatchCreateRequest.model_validate_json(data)
    # 读取每个上传文件，构建与 items 下标对齐的 file_payloads 列表。
    # items[i] 的图纸是 file_payloads[i]；None = 该行无图。
    # 前端可省略 file（或 length < len(items)），按 None 补齐。
    file_payloads: list[tuple[bytes, str, str | None] | None] = []
    if files:
        for f in files:
            raw = await f.read()
            file_payloads.append(
                (raw, f.filename or "drawing.pdf", f.content_type)
            )
        while len(file_payloads) < len(payload.items):
            file_payloads.append(None)
    else:
        file_payloads = [None] * len(payload.items)
    return await svc.create_parts_batch(payload, file_payloads=file_payloads)


@router.post(
    "/batch-with-pdfs",
    response_model=PartBatchTreeResult,
    summary=(
        "批量树形创建（PDF 自动按页拆分；单页 PDF=独立零件；"
        "多页 PDF=装配件+子件，可选 master）（MANAGER / CLERK）"
    ),
    description=(
        "multipart/form-data：`data` 是 PartBatchTreeRequest 的 JSON 字符串；"
        "`files` 是 PDF 数组，按 items[i].pdf_index 对齐。"
        "单页 PDF：items 一条 → 1 个独立零件（kind=DRAWING）。"
        "多页 PDF：assemblies 一条 + items N 条 → 1 个装配件 + N 个子件，"
        "其中 is_master=true 的那条作为 ASSEMBLY_MASTER 上传，"
        "其它作为各子件 DRAWING 上传。"
        "任一上传失败 → 整批回滚；前置校验失败 → 返回 failed 列表（不写盘）。"
        "2026-07-21 新增；替代已退役的 POST /assemblies。"
    ),
    dependencies=_office_dep,
)
async def create_parts_tree(
    data: str = Form(..., description="PartBatchTreeRequest 的 JSON 字符串"),
    files: list[UploadFile] | None = File(
        default=None,
        description="PDF 数组，按 items[i].pdf_index 对齐；可少于 items 长度（缺位报 failed）。",
    ),
    three_d_models: list[UploadFile] | None = File(
        default=None,
        description=(
            "3D 模型数组（.step/.stp/.iges/.igs/.stl/.obj/.3mf），"
            "按 items[i].three_d_index 对齐；PR-H 2026-07-28 新增。"
        ),
    ),
    svc: PartService = Depends(get_part_service),
    part_file_svc: PartFileService = Depends(get_part_file_service),
    applicant_svc: ApplicantService = Depends(get_applicant_service),
) -> PartBatchTreeResult:
    payload = PartBatchTreeRequest.model_validate_json(data)
    file_payloads: dict[int, tuple[bytes, str, str | None]] = {}
    if files:
        for idx, f in enumerate(files):
            raw = await f.read()
            file_payloads[idx] = (
                raw, f.filename or f"page-{idx}.pdf", f.content_type,
            )
    three_d_payloads: dict[int, tuple[bytes, str, str | None]] = {}
    if three_d_models:
        for idx, f in enumerate(three_d_models):
            raw = await f.read()
            three_d_payloads[idx] = (
                raw, f.filename or f"model-{idx}.step", f.content_type,
            )
    return await svc.create_parts_tree(
        payload,
        file_payloads_by_pdf_index=file_payloads,
        three_d_payloads_by_index=three_d_payloads,
        part_files=part_file_svc,
        applicants=applicant_svc,
    )


@router.post(
    "/{part_id}/update",
    response_model=PartOut,
    summary="编辑零件基本信息（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def update_part(
    part_id: int,
    payload: PartUpdateRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.update_part(part_id, payload)


@router.get(
    "/pending-programming",
    response_model=PartListOut,
    summary="待编程一览：status=PROGRAMMING 的零件（MANAGER / CLERK / CNC_PROGRAMMER）",
    dependencies=_read_lots_dep,
)
async def list_pending_programming_parts(
    customer_id: str | None = Query(default=None, description="客户 id（雪花 ID 字符串）"),
    keyword: str | None = Query(
        default=None,
        description="搜索关键字（图号 ILIKE 包含 %kw%；名称 ILIKE 前缀 kw%）",
    ),
    sort_by: str = Query(default="PLANNED_DELIVERY_DATE", description="排序字段"),
    sort_dir: str = Query(default="ASC", description="排序方向"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: PartService = Depends(get_part_service),
) -> PartListOut:
    from model.enums import PartSortKey, PartStatus, SortDir

    return await svc.list_parts(
        PartListQuery(
            customer_id=customer_id,
            statuses=[PartStatus.PROGRAMMING],
            is_urgent=None,
            keyword=keyword,
            sort_by=PartSortKey(sort_by),
            sort_dir=SortDir(sort_dir),
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/outsource-in-flight",
    response_model=list[OutsourceInFlightItem],
    summary="外协中批次列表（2026-07-30 新增；MANAGER / CLERK）",
)
async def list_outsource_in_flight(
    keyword: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> list[OutsourceInFlightItem]:
    items, _ = await svc.list_in_flight(
        keyword=keyword, limit=limit, offset=offset,
    )
    return items


@router.get(
    "/outsource-sendable",
    response_model=OutsourceSendableListOut,
    summary=(
        "外协可发送一览（统一查询，2026-07-28 新增）：合并 APPROVAL（有 APPROVED 报价）和 "
        "DIRECT（无需审批可直发）两类。send_mode 字段区分；source_status 区分起始外协（PENDING）和"
        "中间外协（IN_PROCESS+PRODUCTION_SHELF）（MANAGER / CLERK / INSPECTOR）"
    ),
    dependencies=_inspector_outsource_dep,
)
async def list_outsource_sendable(
    keyword: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: PartService = Depends(get_part_service),
) -> OutsourceSendableListOut:
    return await svc.list_outsource_sendable(
        keyword=keyword, customer_id=customer_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/inspection-batches",
    response_model=InspectionBatchListOut,
    summary="品检待办（批次级；MANAGER / INSPECTOR）",
    description=(
        "2026-07-29 批次化：行=批次（INSPECTION 状态），同一工单多个品检批次各占一行；"
        "pass/fail 操作回传 batch_id。须注册在 /{part_id} 之前避免被 catch-all 截胡。"
    ),
    dependencies=_inspector_dep,
)
async def list_inspection_batches(
    keyword: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    limit: int = Query(default=200, le=500),
    offset: int = Query(default=0, ge=0),
    svc: PartService = Depends(get_part_service),
) -> InspectionBatchListOut:
    items, total = await svc.list_inspection_batches(
        keyword=keyword, customer_id=customer_id, limit=limit, offset=offset,
    )
    return InspectionBatchListOut(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{part_id}",
    response_model=PartOut,
    summary="零件详情（MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR）",
    dependencies=_read_part_dep,
)
async def get_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.get_part(part_id)


@router.post(
    "/{part_id}/soft-delete",
    summary="软删零件（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def soft_delete_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> dict:
    await svc.soft_delete_part(part_id)
    return {"ok": True}


@router.post(
    "/{part_id}/place-on-shelf",
    response_model=PartOut,
    summary="PENDING → IN_PROCESS：把零件放到生产货架（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def place_part_on_shelf(
    part_id: int,
    payload: PlaceOnShelfRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.place_on_shelf(part_id, payload)


@router.post(
    "/{part_id}/send-to-programming",
    response_model=PartOut,
    summary="PENDING → PROGRAMMING：把零件发送至 CNC 编程（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def send_part_to_programming(
    part_id: int,
    payload: PartBatchActionRequest | None = None,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    batch_id, quantity = _batch_action(payload)
    return await svc.send_to_programming(
        part_id, batch_id=batch_id, quantity=quantity,
    )


def _batch_action(
    payload: PartBatchActionRequest | None,
) -> tuple[int | None, int | None]:
    """可选 body → (batch_id int|None, quantity int|None)。"""
    if payload is None:
        return None, None
    batch_id = (
        parse_snowflake_id(payload.batch_id, field_name="batch_id")
        if payload.batch_id else None
    )
    return batch_id, payload.quantity


@router.post(
    "/{part_id}/release-from-programming",
    response_model=PartOut,
    summary="PROGRAMMING → IN_PROCESS：编程员下发到生产货架（MANAGER / CNC_PROGRAMMER）",
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CNC_PROGRAMMER))
    ],
)
async def release_part_from_programming(
    part_id: int,
    payload: PlaceOnShelfRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.release_from_programming(part_id, payload)


# ============================================================
# 外协流程（2026-07-15 新增）
# ============================================================
@router.post(
    "/{part_id}/send-to-outsource",
    response_model=PartOut,
    summary=(
        "PENDING / IN_PROCESS → OUTSOURCE：发送零件到外协公司（MANAGER / CLERK / INSPECTOR）"
    ),
    description=(
        "body: outsource_company_id (雪花 ID 字符串) + next_process_id (OUTSOURCE 类别)。"
        "支持来源：PENDING / ON_SHELF / WITH_WORKER。"
        "后端严格校验公司存在 + 启用 + 工序 OUTSOURCE + 公司映射了该工序。"
        "INSPECTOR 走扫码批量发送（PR-I 2026-07-20）。"
    ),
    dependencies=_inspector_outsource_dep,
)
async def send_part_to_outsource(
    part_id: int,
    payload: SendToOutsourceRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.send_to_outsource(part_id, payload)


@router.post(
    "/{part_id}/receive-from-outsource",
    response_model=PartOut,
    summary=(
        "OUTSOURCE → IN_PROCESS：外协回收，下发到生产货架（MANAGER / CLERK / INSPECTOR）"
    ),
    description=(
        "body 同下发：shelf_id (PRODUCTION 区 active) + next_process_id (必须 INHOUSE)。"
        "INSPECTOR 走扫码批量接收（PR-I 2026-07-20）。"
    ),
    dependencies=_inspector_outsource_dep,
)
async def receive_part_from_outsource(
    part_id: int,
    payload: PlaceOnShelfRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.receive_from_outsource(part_id, payload)


@router.post(
    "/{part_id}/receive-from-outsource-to-inspection",
    response_model=PartOut,
    summary=(
        "OUTSOURCE → INSPECTION：外协件直接送检（MANAGER / CLERK / INSPECTOR，2026-07-16 新增）"
    ),
    description=(
        "body: shelf_id (INSPECTION 区 active 货架) + auto_pass_inspection (可选)。"
        "auto_pass_inspection=true 时一次性 OUTSOURCE → INSPECTION → READY_TO_SHIP。"
        "INSPECTOR 走扫码批量接收（PR-I 2026-07-20）。"
    ),
    dependencies=_inspector_outsource_dep,
)
async def receive_part_from_outsource_to_inspection(
    part_id: int,
    payload: ReceiveToInspectionRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.receive_from_outsource_to_inspection(part_id, payload)


@router.post(
    "/{part_id}/pass-inspection",
    response_model=PartOut,
    summary="INSPECTION → READY_TO_SHIP：品检合格（MANAGER / INSPECTOR）",
    dependencies=_inspector_dep,
)
async def pass_part_inspection(
    part_id: int,
    payload: PartBatchActionRequest | None = None,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    batch_id, quantity = _batch_action(payload)
    return await svc.pass_inspection(
        part_id, batch_id=batch_id, quantity=quantity,
    )


@router.post(
    "/{part_id}/fail-inspection",
    response_model=PartOut,
    summary="INSPECTION → IN_PROCESS：品检不通过，打回生产货架（含备注，MANAGER / INSPECTOR）",
    description=(
        "2026-07-21 改：品检员在 INSPECTION 状态下点击打回，同时指定目标生产货架 + 下一道工序；"
        "service 端校验 `t_shelf_process` 映射（`BIZ_SHELF_PROCESS_NOT_MAPPED` 422，"
        "与 place_on_shelf / release_from_programming 对齐）。可选 `note` 写"
        "入 t_part_event.note，事件历史一览与工人扫码领取列表均可见。"
    ),
    dependencies=_inspector_dep,
)
async def fail_part_inspection(
    part_id: int,
    payload: FailInspectionRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.fail_inspection(part_id, payload)


@router.post(
    "/{part_id}/deliver",
    response_model=PartOut,
    summary="READY_TO_SHIP → DELIVERED：发货（MANAGER / CLERK；手动调用）",
    description=(
        "文员/管理员手动调用：service 走通用路径，actual_delivery_date 默"
        "认写今天。司机扫码签收请走 `/scan/deliver-part` 端点（PR-C 2026-07-10）。"
    ),
    dependencies=_office_dep,
)
async def deliver_part(
    part_id: int,
    payload: PartBatchActionRequest | None = None,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    batch_id, quantity = _batch_action(payload)
    return await svc.deliver(part_id, batch_id=batch_id, quantity=quantity)


# ============================================================
# 扫码台：司机确认发货（PR-C 2026-07-10）
# ============================================================
class DeliverByDriverRequest(BaseModel):
    """扫码台：司机确认发货请求体。

    - `part_id` 是雪花 ID 字符串（前端从流水号反查得到后传入）。
    - `worker_badge_code` 是前置「扫工牌」得到的送货司机工牌码；
      service 层校验 `t_worker.work_type.code == '送货司机'`。
    """

    part_id: str = Field(description="雪花 ID 字符串")
    worker_badge_code: str = Field(min_length=1, max_length=50, description="司机工牌码")


@router.post(
    "/scan/deliver-part",
    response_model=PartOut,
    summary="扫码台：司机确认发货（任意已登录 + worker_badge_code 必须是送货司机）",
    description=(
        "权限 `require_auth()`：一体机 SHELF_ACCOUNT 账号即可调用。"
        "真正的鉴权点在 service 层 —— 仅当 worker_badge_code 对应的 t_worker "
        "工种 = '送货司机' 才允许触发 DELIVERED 转换；其他人调用 400。"
    ),
    dependencies=[Depends(require_auth())],
)
async def scan_deliver_part(
    payload: DeliverByDriverRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    part_id_int = parse_snowflake_id(payload.part_id, field_name="part_id")
    return await svc.deliver(
        part_id=part_id_int,
        actual_delivery_date=None,
        worker_badge_code=payload.worker_badge_code,
    )


@router.post(
    "/{part_id}/complete",
    response_model=PartOut,
    summary="DELIVERED → COMPLETED：确认完成，释放流水号（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def complete_part(
    part_id: int,
    payload: PartBatchActionRequest | None = None,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    batch_id, _quantity = _batch_action(payload)
    return await svc.complete(part_id, batch_id=batch_id)


@router.post(
    "/{part_id}/start-repair",
    response_model=PartOut,
    summary="→ REPAIRING：开始返修（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def start_part_repair(
    part_id: int,
    payload: PartBatchActionRequest | None = None,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    batch_id, quantity = _batch_action(payload)
    return await svc.start_repair(
        part_id, batch_id=batch_id, quantity=quantity,
    )


@router.post(
    "/{part_id}/complete-repair",
    response_model=PartOut,
    summary="REPAIRING → IN_PROCESS：返修完成（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def complete_part_repair(
    part_id: int,
    shelf_id: int = Query(..., description="目标生产货架 id"),
    batch_id: int | None = Query(default=None, description="目标批次 id（可选）"),
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.complete_repair(part_id, shelf_id, batch_id=batch_id)


@router.post(
    "/{part_id}/cancel",
    response_model=PartOut,
    summary="→ CANCELLED：取消零件，释放流水号（MANAGER / CLERK）",
    dependencies=_office_dep,
)
async def cancel_part(
    part_id: int,
    payload: PartBatchActionRequest | None = None,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    batch_id, _quantity = _batch_action(payload)
    return await svc.cancel(part_id, batch_id=batch_id)


@router.get(
    "/{part_id}/events",
    response_model=list[PartEventOut],
    summary="该零件的全生命周期事件流（MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR）",
    dependencies=_read_part_dep,
)
async def list_part_events(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> list[PartEventOut]:
    return await svc.list_events(part_id)


# ============================================================
# 批次监控 / 拆分 / 取消（2026-07-29 批次化）
# ============================================================
@router.get(
    "/{part_id}/batches",
    response_model=list[PartBatchOut],
    summary="工单的全部批次（详情页批次监控；MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR）",
    dependencies=_read_part_dep,
)
async def list_part_batches(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> list[PartBatchOut]:
    return await svc.list_batches(part_id)


@router.post(
    "/{part_id}/batches/split",
    response_model=list[PartBatchOut],
    summary="手动拆分批次（MANAGER / CLERK）；返回最新批次列表",
    dependencies=_office_dep,
)
async def split_part_batch(
    part_id: int,
    payload: BatchSplitRequest,
    svc: PartService = Depends(get_part_service),
) -> list[PartBatchOut]:
    batch_id = parse_snowflake_id(payload.batch_id, field_name="batch_id")
    if batch_id is None:
        raise BizError(
            code=ErrCode.BIZ_PART_BATCH_NOT_FOUND,
            message=f"batch_id 不是合法的雪花 ID：{payload.batch_id!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    return await svc.split_batch(
        part_id, batch_id=batch_id, quantity=payload.quantity,
    )


@router.post(
    "/{part_id}/batches/{batch_id}/cancel",
    response_model=list[PartBatchOut],
    summary="取消单个批次（MANAGER / CLERK）；返回最新批次列表",
    dependencies=_office_dep,
)
async def cancel_part_batch(
    part_id: int,
    batch_id: int,
    svc: PartService = Depends(get_part_service),
) -> list[PartBatchOut]:
    return await svc.cancel_batch(part_id, batch_id=batch_id)


# ============================================================
# SHELF_ACCOUNT 路由（扫码台用）
# ============================================================
@router.post(
    "/pick-up",
    response_model=PartOut,
    summary="工人扫码领取（SHELF_ACCOUNT @ 该 shelf）：holder shelf → worker",
)
async def pick_up_part(
    payload: PartPickUpRequest,
    ctx: tuple[CurrentUser, int] = Depends(
        require_shelf_account_from_body("shelf_id")
    ),
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    _user, _shelf_id = ctx
    return await svc.pick_up_by_scan(payload)


@router.post(
    "/scan",
    response_model=PartOut,
    summary="工人扫图纸归还 / 送检（SHELF_ACCOUNT @ 该 shelf）",
    description=(
        "2026-07-13 起：对 INSPECTED 事件 + `target_inspection_shelf_id`，"
        "做 user scope 校验（can_operate_shelf）—— SHELF_ACCOUNT 只能送"
        "到自己绑定的品检架；MANAGER / wildcard 放行。"
    ),
)
async def scan_part(
    payload: PartScanRequest,
    ctx: tuple[CurrentUser, int] = Depends(
        require_shelf_account_from_body("shelf_id")
    ),
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    _ctx_user, _shelf_id = ctx
    # 2026-07-13: 补 target_inspection_shelf_id 的 user scope 校验
    if (
        payload.event_type == PartEventType.INSPECTED
        and payload.target_inspection_shelf_id is not None
    ):
        tid = int(payload.target_inspection_shelf_id)
        if not _ctx_user.can_operate_shelf(tid):
            raise BizError(
                code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                message="target inspection shelf not in user's scope",
                http_status=http_status.HTTP_403_FORBIDDEN,
            )
    return await svc.scan_event(payload)


# ============================================================
# 任意已登录用户可调（SHELF_ACCOUNT 扫码台按 serial 查零件用）
# ============================================================
@router.get(
    "/by-serial/{serial_no}",
    response_model=PartOut,
    summary="按序列号定位零件（任意已登录用户）",
    dependencies=[Depends(require_auth())],
)
async def get_part_by_serial(
    serial_no: str,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    part = await svc.parts.get_by_serial(serial_no)
    if part is None:
        raise BizError(
            code=ErrCode.BIZ_PART_NOT_FOUND,
            message=f"serial {serial_no} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    items = await svc._to_out([part])
    return items[0]


@router.get(
    "/by-work-type/{work_type_id}",
    response_model=list[PartOut],
    summary="扫码台 PICK_UP 列表：当前货架上某工种可领的零件",
    description=(
        "按工种 id 列出生产货架上、下一道工序属于该工种映射的零件。"
        "排序：加急优先 → 临期优先 → id 降序。"
        "返回 [] 时前端提示「无可领件 / 工种未映射 / 未分配工种」。"
        "2026-07-13 起：scoped SHELF_ACCOUNT 越权 shelf_id → 短路空（防越权）。"
    ),
    dependencies=[Depends(require_auth())],
)
async def list_pickable_parts_by_work_type(
    work_type_id: int,
    shelf_id: int = Query(..., description="当前操作的生产货架 id"),
    user: CurrentUser = Depends(get_current_user),
    svc: PartService = Depends(get_part_service),
) -> list[PartOut]:
    # 2026-07-13: 越权 shelf_id 短路（非 HMI 角色或不在 scope 内）
    if not user.has_role(UserRole.MANAGER) and not user.shelf_wildcard:
        if (
            not user.has_role(UserRole.SHELF_ACCOUNT)
            or shelf_id not in user.shelf_ids
        ):
            return []
    return await svc.list_pickable_parts(work_type_id, shelf_id)


# 共享 HMI PICK_UP 跨架列表（2026-07-10；2026-07-10 加 HMI scope 过滤）
@router.get(
    "/pickable-by-work-type/{work_type_id}",
    response_model=list[PartOut],
    summary="共享 HMI PICK_UP 跨架列表：HMI 货架范围内某工种可领的零件",
    description=(
        "按工种 id 列出 HMI 货架范围内、下一道工序属于该工种映射（或未指定"
        "下一道工序）的零件。前端按 `current_holder_id` 在卡片网格里分组。"
        "HMI scope 规则：MANAGER 看全架；SHELF_ACCOUNT@wildcard 看全架；"
        "SHELF_ACCOUNT@scope 仅看自己绑定的架；其他角色 → []。"
        "用于共享工控机场景：工人刷工牌后看到所有候选架的分组列表。"
    ),
    dependencies=[Depends(require_auth())],
)
async def list_pickable_parts_by_work_type_all_shelves(
    work_type_id: int,
    user: CurrentUser = Depends(get_current_user),
    svc: PartService = Depends(get_part_service),
) -> list[PartOut]:
    # HMI scope 过滤：MANAGER 走全架；SHELF_ACCOUNT 走 scope / wildcard；
    # 其他角色（CLERK / CNC / INSPECTOR 等）→ []（不应调此端点）。
    shelf_ids: list[int] | None = None
    if not user.has_role(UserRole.MANAGER):
        if user.has_role(UserRole.SHELF_ACCOUNT):
            shelf_ids = None if user.shelf_wildcard else list(user.shelf_ids)
        else:
            shelf_ids = []  # 非 HMI 角色 → 空（repository 短路）
    return await svc.list_pickable_parts_all_shelves(
        work_type_id, shelf_ids=shelf_ids,
    )


@router.get(
    "/by-worker/{worker_id}",
    response_model=list[PartOut],
    summary="扫码台 RETURN 列表：某工人当前持有的所有零件",
    description=(
        "列出当前 location=WORKER 且 current_holder_id=worker_id 的零件。"
        "排序：加急优先 → 临期优先 → id 降序。"
        "返回 [] 时前端提示「当前无持有零件」。"
        "权限：任意已登录用户（扫码台客户端已用 useScanSession 拿到 worker.id）。"
        "worker_id 入参是雪花 ID 字符串，转 int（CLAUDE.md §3）。"
    ),
    dependencies=[Depends(require_auth())],
)
async def list_parts_held_by_worker(
    worker_id: str,
    svc: PartService = Depends(get_part_service),
) -> list[PartOut]:
    worker_id_int = parse_snowflake_id(worker_id, field_name="worker_id")
    return await svc.list_parts_held_by_worker(worker_id_int)

# ============================================================
# 双面打印 PDF（图纸 + 反面条形码）
# ============================================================
# 权限：CLERK + MANAGER + CNC_PROGRAMMER（文员下发 + 管理员补打 + 编程员打印图纸）。
@router.get(
    "/{part_id}/print-drawing",
    summary="生成零件的双面打印 PDF（图纸 + 反面右下角条形码）",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        },
    },
    dependencies=_office_dep,
)
async def print_part_drawing(
    part_id: int,
    parts: PartRepository = Depends(get_part_repository),
    part_files: PartFileRepository = Depends(get_part_file_repository),
) -> Response:
    pdf_bytes = await build_part_print_pdf(
        part_id=part_id, parts=parts, part_files=part_files,
    )
    # 文件名建议：serial_no + drawing_no，便于纸面贴标查找
    part = await parts.get_by_id(part_id)
    serial = part.serial_no if part and part.serial_no else "no-serial"
    drawing = part.drawing_no if part and part.drawing_no else "part"
    fname = f"{serial}-{drawing}.pdf".replace("/", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


# ============================================================
# 批量打印 PDF（2026-07-17 接入：合并多 part 双面 PDF 为单 PDF；2026-07-30 扩展装配件）
# ============================================================
class PrintBatchRequest(BaseModel):
    """批量打印请求体（雪花 ID 字符串列表，service 层 int() 转换）。"""

    part_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="零件雪花 ID 字符串列表（0-200 个）",
    )
    assembly_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="装配件雪花 ID 字符串列表（0-50 个）；打印其总装图 + 全部子件",
    )


@router.post(
    "/print-drawing-batch",
    summary="批量生成零件的双面打印 PDF 并合并为一个 PDF（支持装配件总装图）",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        },
    },
    dependencies=_office_dep,
)
async def print_part_drawing_batch(
    payload: PrintBatchRequest,
    parts: PartRepository = Depends(get_part_repository),
    part_files: PartFileRepository = Depends(get_part_file_repository),
    assemblies: AssemblyRepository = Depends(get_assembly_repo),
) -> Response:
    # str → int 转换；任一失败抛 BIZ_INVALID_VALUE 400（与 CLAUDE.md §3 约定一致）
    part_ids_int: list[int] = []
    for s in payload.part_ids:
        try:
            part_ids_int.append(int(s))
        except (TypeError, ValueError) as e:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"part_id 必须是数字字符串：{s!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ) from e

    assembly_ids_int: list[int] = []
    for s in payload.assembly_ids:
        try:
            assembly_ids_int.append(int(s))
        except (TypeError, ValueError) as e:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"assembly_id 必须是数字字符串：{s!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ) from e

    if not part_ids_int and not assembly_ids_int:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message="part_ids 与 assembly_ids 至少提供一个",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )

    pdf_bytes = await build_parts_print_pdf_batch(
        part_ids=part_ids_int,
        assembly_ids=assembly_ids_int or None,
        parts=parts,
        part_files=part_files,
        assemblies=assemblies,
    )
    total_items = len(part_ids_int) + len(assembly_ids_int)
    fname = f"batch-{total_items}items-{now_naive().strftime('%Y%m%d%H%M%S')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )
