"""外协报价 (OutsourceQuote) API（2026-07-16 新增）。

路由结构：
- **读路由**（GET）：MANAGER + CLERK
- **写路由**（POST）：除 approve / reject 是 MANAGER-only 外，其余 MANAGER + CLERK

接口清单：
- GET   /outsource-quotes                  列表
- POST  /outsource-quotes                  新建 DRAFT
- GET   /outsource-quotes/{id}             详情
- POST  /outsource-quotes/{id}/update      修改（DRAFT only）
- POST  /outsource-quotes/{id}/submit      提交审核（DRAFT → SUBMITTED）
- POST  /outsource-quotes/{id}/approve     通过（SUBMITTED → APPROVED，MANAGER-only）
- POST  /outsource-quotes/{id}/reject      拒绝（SUBMITTED → REJECTED，MANAGER-only）
- POST  /outsource-quotes/{id}/soft-delete 软删（DRAFT / REJECTED）
- GET   /outsource-quotes/approved-for-send 外协发送列表页数据
"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_outsource_quote_service
from core.permission import require_role, require_roles
from model.enums import OutsourceQuoteSortKey, OutsourceQuoteStatus, SortDir, UserRole
from schema.outsource_quote import (
    ApprovedForSendListOut,
    OutsourceQuoteApproveRequest,
    OutsourceQuoteCreateRequest,
    OutsourceQuoteListOut,
    OutsourceQuoteListQuery,
    OutsourceQuoteOut,
    OutsourceQuoteRejectRequest,
    OutsourceQuoteUpdateRequest,
)
from service import OutsourceQuoteService


# ============================================================
# 读路由：MANAGER + CLERK
# ============================================================
read_router = APIRouter(
    prefix="/outsource-quotes",
    tags=["外协报价(读)"],
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CLERK)),
    ],
)


@read_router.get(
    "",
    response_model=OutsourceQuoteListOut,
    summary="外协报价列表（MANAGER / CLERK）",
)
async def list_outsource_quotes(
    status: OutsourceQuoteStatus | None = None,
    statuses: list[OutsourceQuoteStatus] | None = Query(
        default=None, description="多选状态（与 status 取并集，SQL IN(...)）",
    ),
    part_id: str | None = None,
    outsource_company_id: str | None = None,
    customer_id: str | None = None,
    keyword: str | None = None,
    sort_by: OutsourceQuoteSortKey = OutsourceQuoteSortKey.CREATED_AT,
    sort_dir: SortDir = SortDir.DESC,
    limit: int = 50,
    offset: int = 0,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteListOut:
    return await svc.list_quotes(OutsourceQuoteListQuery(
        status=status, statuses=statuses,
        part_id=part_id, outsource_company_id=outsource_company_id,
        customer_id=customer_id, keyword=keyword,
        sort_by=sort_by, sort_dir=sort_dir,
        limit=limit, offset=offset,
    ))


@read_router.get(
    "/approved-for-send",
    response_model=ApprovedForSendListOut,
    summary=(
        "外协发送列表：至少有 1 条 APPROVED 报价 + 状态可发送的零件"
        "（MANAGER / CLERK）"
    ),
)
async def list_approved_for_send(
    keyword: str | None = None,
    customer_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> ApprovedForSendListOut:
    return await svc.list_approved_for_send(
        keyword=keyword, customer_id=customer_id,
        limit=limit, offset=offset,
    )


@read_router.get(
    "/{quote_id}",
    response_model=OutsourceQuoteOut,
    summary="外协报价详情（MANAGER / CLERK）",
)
async def get_outsource_quote(
    quote_id: str,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteOut:
    return await svc.get_quote(quote_id)


# ============================================================
# 写路由：CLERK + MANAGER（除 approve / reject 是 MANAGER-only）
# ============================================================
write_router = APIRouter(
    prefix="/outsource-quotes",
    tags=["外协报价(写)"],
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CLERK)),
    ],
)


@write_router.post(
    "",
    response_model=OutsourceQuoteOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新建外协报价 DRAFT（MANAGER / CLERK）",
)
async def create_outsource_quote(
    payload: OutsourceQuoteCreateRequest,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteOut:
    return await svc.create_quote(payload)


@write_router.post(
    "/{quote_id}/update",
    response_model=OutsourceQuoteOut,
    summary="修改 DRAFT 报价的字段（需 version 乐观锁，MANAGER / CLERK）",
)
async def update_outsource_quote(
    quote_id: str,
    payload: OutsourceQuoteUpdateRequest,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteOut:
    return await svc.update_quote(quote_id, payload)


@write_router.post(
    "/{quote_id}/submit",
    response_model=OutsourceQuoteOut,
    summary="DRAFT → SUBMITTED 提交审核（MANAGER / CLERK）",
)
async def submit_outsource_quote(
    quote_id: str,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteOut:
    return await svc.submit_quote(quote_id)


@write_router.post(
    "/{quote_id}/approve",
    response_model=OutsourceQuoteOut,
    summary="SUBMITTED → APPROVED 审批通过（**MANAGER-only**）",
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)
async def approve_outsource_quote(
    quote_id: str,
    payload: OutsourceQuoteApproveRequest,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteOut:
    return await svc.approve_quote(quote_id, payload)


@write_router.post(
    "/{quote_id}/reject",
    response_model=OutsourceQuoteOut,
    summary="SUBMITTED → REJECTED 审批拒绝（review_note 必填，**MANAGER-only**）",
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)
async def reject_outsource_quote(
    quote_id: str,
    payload: OutsourceQuoteRejectRequest,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceQuoteOut:
    return await svc.reject_quote(quote_id, payload)


@write_router.post(
    "/{quote_id}/soft-delete",
    summary="软删报价（DRAFT / REJECTED only，MANAGER / CLERK）",
)
async def soft_delete_outsource_quote(
    quote_id: str,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> dict:
    await svc.soft_delete_quote(quote_id)
    return {"ok": True}
