"""送货单管理 API（2026-07-22 新增；替代老 `/delivery-notes/generate`）。

形态对齐 OutsourcQuote/Part 等业务路由：FastAPI 依赖注入 + global error handler
信封；只允许 GET / POST（CLAUDE.md §7）。

权限策略：
- 文员侧 (CLERK + MANAGER + INSPECTOR)：list / detail / events / create /
  add-parts / remove-parts / submit / recall / soft-delete / print
- 司机侧（任意已登录账号 + service 层校验 worker.work_type='送货司机'）：
  pickup-pending list / detail / pickup-scan / pickup

`/delivery-notes/generate` 老 XLSX 路由已下线（PR-G 替换方案）。
"""
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_delivery_note_service
from core.permission import (
    CurrentUser,
    get_current_user,
    require_auth,
    require_roles,
)
from core.time import now_naive
from model.enums import (
    DeliveryNoteSortKey,
    SortDir,
    UserRole,
)
from schema.delivery_note import (
    DeliveryNoteAddPartsRequest,
    DeliveryNoteCandidatePartsOut,
    DeliveryNoteCreateRequest,
    DeliveryNoteDetailOut,
    DeliveryNoteEventOut,
    DeliveryNoteListOut,
    DeliveryNoteOut,
    DeliveryNotePickupListOut,
    DeliveryNotePickupRequest,
    DeliveryNotePickupScanOut,
    DeliveryNotePickupScanRequest,
    DeliveryNoteRemovePartsRequest,
    DeliveryNoteUpdateRequest,
    DeliveryNoteVersionedRequest,
)
from service.delivery_note import DeliveryNoteService

router = APIRouter(prefix="/delivery-notes", tags=["送货单"])

_OFFICE_DEP = [
    Depends(
        require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.INSPECTOR,
        )
    )
]

_OFFICE_ROLES = (UserRole.MANAGER, UserRole.CLERK, UserRole.INSPECTOR)


# ============================================================
# 1. 一览 / 详情 / 待送货 / 候选零件
# ============================================================

# 1a. GET /delivery-notes/candidate-parts —— 字面子路径先注册，避免被 /{note_id}
#     catch-all 截胡（CLAUDE.md §shelves 同款约束）
@router.get(
    "/candidate-parts",
    response_model=DeliveryNoteCandidatePartsOut,
    summary="可选入单的零件（INSPECTION + READY_TO_SHIP，同 L1 根、不在 active 单上）",
    dependencies=_OFFICE_DEP,
)
async def list_candidate_parts(
    customer_id: Annotated[str, Query(description="一级客户雪花 ID (L1 root)")],
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteCandidatePartsOut:
    items = await svc.list_candidate_parts(customer_id)
    return DeliveryNoteCandidatePartsOut(items=items)


@router.get(
    "",
    response_model=DeliveryNoteListOut,
    summary="送货单一览（statuses / customer_id / keyword 过滤 + 分页）",
    dependencies=_OFFICE_DEP,
)
async def list_delivery_notes(
    statuses: list[str] | None = None,
    customer_id: str | None = None,
    keyword: str | None = None,
    sort_by: DeliveryNoteSortKey = DeliveryNoteSortKey.CREATED_AT,
    sort_dir: SortDir = SortDir.DESC,
    limit: int = 50,
    offset: int = 0,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteListOut:
    items, total = await svc.list_with_filters(
        statuses=statuses,
        customer_id=customer_id,
        keyword=keyword,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return DeliveryNoteListOut(
        items=items, total=total, limit=limit, offset=offset,
    )


@router.get(
    "/pickup-pending",
    response_model=DeliveryNotePickupListOut,
    summary=(
        "司机待送货一览（仅 SUBMITTED 的非软删单；service 层校验 driver "
        "work_type='送货司机'）"
    ),
    dependencies=[Depends(require_auth())],
)
async def list_pickup_pending(
    customer_id: str | None = None,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNotePickupListOut:
    items = await svc.list_for_pickup(customer_id=customer_id)
    return DeliveryNotePickupListOut(items=items)


@router.post(
    "",
    response_model=DeliveryNoteOut,
    summary="创建送货单草稿（CLERK / MANAGER）",
    status_code=201,
    dependencies=_OFFICE_DEP,
)
async def create_delivery_note(
    payload: DeliveryNoteCreateRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
    _user: CurrentUser = Depends(require_auth()),
) -> DeliveryNoteOut:
    return await svc.create_draft(
        customer_id=payload.customer_id,
        note=payload.note,
        delivery_date=payload.delivery_date,
        initial_items=payload.items or None,
    )


@router.get(
    "/{note_id}",
    response_model=DeliveryNoteDetailOut,
    summary="送货单详情（line_items + scanned_serials）",
    dependencies=[Depends(require_auth())],
)
async def get_delivery_note(
    note_id: str,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteDetailOut:
    return await svc.get_with_parts(note_id)


@router.get(
    "/{note_id}/events",
    response_model=list[DeliveryNoteEventOut],
    summary="送货单事件时间线",
    dependencies=[Depends(require_auth())],
)
async def list_delivery_note_events(
    note_id: str,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> list[DeliveryNoteEventOut]:
    return await svc.list_events(note_id)


# ============================================================
# 2. 草稿期 add / remove 零件 / partial update（CLERK / MANAGER）
# ============================================================

# 2026-07-23：partial update（送货日期 / 备注），DRAFT/SUBMITTED 可编辑
@router.post(
    "/{note_id}/update",
    response_model=DeliveryNoteOut,
    summary="partial update（CLERK / MANAGER；DRAFT / SUBMITTED）",
    dependencies=_OFFICE_DEP,
)
async def update_delivery_note(
    note_id: str,
    payload: DeliveryNoteUpdateRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteOut:
    return await svc.update(
        note_id=note_id,
        version=payload.version,
        delivery_date=payload.delivery_date,
        note_text=payload.note,
    )


@router.post(
    "/{note_id}/add-parts",
    response_model=DeliveryNoteDetailOut,
    summary="添加零件（CLERK / MANAGER；DRAFT / SUBMITTED）",
    dependencies=_OFFICE_DEP,
)
async def add_delivery_note_parts(
    note_id: str,
    payload: DeliveryNoteAddPartsRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteDetailOut:
    return await svc.add_parts(
        note_id=note_id, items=payload.items, version=payload.version,
    )


@router.post(
    "/{note_id}/remove-parts",
    response_model=DeliveryNoteDetailOut,
    summary="移除零件（CLERK / MANAGER；DRAFT / SUBMITTED）",
    dependencies=_OFFICE_DEP,
)
async def remove_delivery_note_parts(
    note_id: str,
    payload: DeliveryNoteRemovePartsRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteDetailOut:
    return await svc.remove_parts(
        note_id=note_id, batch_ids=payload.batch_ids, version=payload.version,
    )


# ============================================================
# 3. 状态机迁移（CLERK / MANAGER）
# ============================================================
@router.post(
    "/{note_id}/submit",
    response_model=DeliveryNoteOut,
    summary="DRAFT → SUBMITTED（CLERK / MANAGER）",
    dependencies=_OFFICE_DEP,
)
async def submit_delivery_note(
    note_id: str,
    payload: DeliveryNoteVersionedRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteOut:
    return await svc.submit(note_id=note_id, version=payload.version)


@router.post(
    "/{note_id}/recall",
    response_model=DeliveryNoteOut,
    summary="SUBMITTED → DRAFT（CLERK / MANAGER 撤回）",
    dependencies=_OFFICE_DEP,
)
async def recall_delivery_note(
    note_id: str,
    payload: DeliveryNoteVersionedRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteOut:
    return await svc.recall(note_id=note_id, version=payload.version)


# ============================================================
# 4. 司机扫码台（任意已登录 + service 层校验 driver work_type='送货司机'）
# ============================================================
@router.post(
    "/{note_id}/pickup-scan",
    response_model=DeliveryNotePickupScanOut,
    summary=(
        "司机每扫一个 part，调一次；返回 {scanned, expected, ready}"
    ),
    dependencies=[Depends(require_auth())],
)
async def pickup_scan(
    note_id: str,
    payload: DeliveryNotePickupScanRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNotePickupScanOut:
    return await svc.pickup_scan(
        note_id=note_id,
        part_serial=payload.part_serial,
        badge_code=payload.badge_code,
    )


@router.post(
    "/{note_id}/pickup",
    response_model=DeliveryNoteOut,
    summary=(
        "扫齐后一次性 finalize：原子完成「全员 part.deliver + 单据 pickup / archive」"
    ),
    dependencies=[Depends(require_auth())],
)
async def pickup_delivery_note(
    note_id: str,
    payload: DeliveryNotePickupRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> DeliveryNoteOut:
    return await svc.pickup(
        note_id=note_id,
        driver_worker_id=payload.driver_worker_id,
        version=payload.version,
        badge_code=payload.badge_code,
    )


# ============================================================
# 5. 软删（仅 DRAFT，CLERK / MANAGER）
# ============================================================
@router.post(
    "/{note_id}/soft-delete",
    summary="软删（仅 DRAFT；CLERK / MANAGER）",
    status_code=204,
    dependencies=_OFFICE_DEP,
)
async def soft_delete_delivery_note(
    note_id: str,
    payload: DeliveryNoteVersionedRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> None:
    await svc.soft_delete(note_id=note_id, version=payload.version)


# ============================================================
# 6. 打印（CLERK / MANAGER；全 4 状态可打）
#    按 L1 客户的序列号前缀分发 template/delivery_note_{prefix}.xlsx。
#
#    GET — Authorization header；XLSX 通过 StreamingResponse 分块 yield（CHUNK_SIZE），
#          让 nginx（proxy_buffering off）把字节逐步推到浏览器，前端 Axios 的
#          `onDownloadProgress` 据此计算进度百分比。Content-Length 仍显式设置
#          （总字节已知，前端需要 `total` 算百分比）。
# ============================================================
CHUNK_SIZE = 64 * 1024  # 64KB，与典型 TCP send buffer 同量级


def _print_filename(prefix: str, kind: str) -> str:
    # 2026-08-07：统一导出文件名 {prefix}-YYYY-MM-DD-(note|label).xlsx
    # 同日同 prefix 同类型重名由浏览器自动加 (1)(2) 处理，后端不维护计数器
    return f"{prefix}-{now_naive():%Y-%m-%d}-{kind}.xlsx"


class PrintDeliveryNoteRequest(BaseModel):
    """2026-08-02 新增：预览确认后导出用；custom_order 为空走默认 DB 顺序。

    2026-08-04 扩展：``merge_assemblies`` 控制装配件子件是否合并为一行（数量 1 套）。
    2026-08-04 扩展：``merge_quantities`` 按装配件 override 套数（≥ 1）。
    """

    custom_order: list[str] = Field(
        default_factory=list,
        description="批次 id 列表（雪花 ID 字符串）；与详情页 line_items.id 一一对应",
    )
    merge_assemblies: bool = Field(
        # 2026-08-07 改默认：单上含装配件子件时直接合并为一套打印，避免散件打印
        default=True,
        description=(
            "True → 同一装配体的子件在送货单上合并为一行（数量 1，单位套，"
            "显示总装图号/装配体序列号/名称）；False → 散件逐行打印。"
            "默认 True；前端预览对话框也默认「合并一套」。"
        ),
    )
    merge_quantities: dict[str, int] | None = Field(
        default=None,
        description=(
            "装配件合并打印时，每套数量 override（assembly_id 雪花 ID 字符串 → 套数，"
            "≥ 1）；缺省 = 1。merge_assemblies=False 时忽略。"
        ),
    )


class PrintLabelsRequest(PrintDeliveryNoteRequest):
    """2026-08-07：标签独立导出，支持只打勾选行。

    line_item_ids 与 custom_order 语义正交：
    - custom_order 决定顺序，仍须包含本单全部行（漏行 422，旧行为不变）
    - line_item_ids 决定成员，是 custom_order 的子集；None = 全打
    """

    line_item_ids: list[str] | None = Field(
        default=None,
        description=(
            "只打印这些批次行（雪花 ID 字符串，对应 line_items.id）；"
            "None = 全部。装配件合并模式下前端需把父行展开为组内子件 id。"
            "空数组 [] 视为非法（400）。"
        ),
    )


@router.post(
    "/{note_id}/print",
    summary=(
        "导出送货单 XLSX（2026-08-02 改 POST + body 携带 custom_order；"
        "DRAFT/SUBMITTED/PICKED_UP/ARCHIVED 全状态可打；"
        "StreamingResponse 让前端 onDownloadProgress 拿到细粒度 loaded 事件）"
    ),
    dependencies=_OFFICE_DEP,
)
async def print_delivery_note(
    note_id: str,
    payload: PrintDeliveryNoteRequest = Body(default=PrintDeliveryNoteRequest()),
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> StreamingResponse:
    xlsx_bytes, prefix = await svc.print_xlsx(
        note_id,
        custom_order=payload.custom_order or None,
        merge_assemblies=payload.merge_assemblies,
        merge_quantities=payload.merge_quantities,
    )
    filename = _print_filename(prefix, "note")

    async def stream_chunks():
        for i in range(0, len(xlsx_bytes), CHUNK_SIZE):
            yield xlsx_bytes[i : i + CHUNK_SIZE]

    return StreamingResponse(
        stream_chunks(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(xlsx_bytes)),
            "Cache-Control": "no-store",
        },
    )


# 2026-08-05 PR-C5：打印标签 Excel（独立文件），与 /print 共用
# PrintDeliveryNoteRequest body，行口径一致（merge_assemblies 自动反映套/件）。
@router.post(
    "/{note_id}/print-labels",
    summary=(
        "导出打印标签 XLSX（2026-08-05 新增；与 /print 配对触发两次下载，"
        "表头 客户/订单号/申请人/名称/图号/数量/单位；行口径与送货单完全一致）"
    ),
    dependencies=_OFFICE_DEP,
)
async def print_delivery_note_labels(
    note_id: str,
    payload: PrintLabelsRequest = Body(default=PrintLabelsRequest()),
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> StreamingResponse:
    xlsx_bytes, prefix = await svc.print_labels_xlsx(
        note_id,
        custom_order=payload.custom_order or None,
        merge_assemblies=payload.merge_assemblies,
        merge_quantities=payload.merge_quantities,
        line_item_ids=payload.line_item_ids,
    )
    filename = _print_filename(prefix, "label")

    async def stream_chunks():
        for i in range(0, len(xlsx_bytes), CHUNK_SIZE):
            yield xlsx_bytes[i : i + CHUNK_SIZE]

    return StreamingResponse(
        stream_chunks(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(xlsx_bytes)),
            "Cache-Control": "no-store",
        },
    )
