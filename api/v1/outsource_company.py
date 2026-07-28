"""外协公司 (OutsourceCompany) API。

路由结构（与 /customers 一致的 read/write 双 router 拆分）：
- **读端点**（GET）：MANAGER + CLERK + CNC_PROGRAMMER
  编程员要拉公司列表做「按工序过滤候选公司」（CNC 也有发外协的可能）。
- **写端点**（POST 创建 / 更新 / 软删 / 维护工序）：MANAGER + CLERK
  与申请人类似，CLERK 也可维护外协公司基础信息 + 工序能力。

另外提供：
- `GET /outsource-companies/by-process/{process_id}`：按工序反查能做此工序的
  活跃公司，给发送外协对话框前端过滤候选用。
- `GET /outsource-companies/{company_id}/sent-parts`（2026-07-28 新增）：外协对账一览，
  按公司聚合 SENT_TO_OUTSOURCE 事件，列出所有送给该公司的零件。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_outsource_company_service
from core.permission import require_roles
from model.enums import OutsourceSentPartSortKey, SortDir, UserRole
from schema.outsource_company import (
    OutsourceCompanyCreateRequest,
    OutsourceCompanyListOut,
    OutsourceCompanyListQuery,
    OutsourceCompanyOut,
    OutsourceCompanyUpdateRequest,
    OutsourceCompanyWithProcessesOut,
    OutsourceSentPartListOut,
    OutsourceSentPartListQuery,
    SetOutsourceCompanyProcessRequest,
)
from service import OutsourceCompanyService


# ============================================================
# 读路由：MANAGER + CLERK + CNC_PROGRAMMER + INSPECTOR
# （PR-I 2026-07-20：INSPECTOR 扫码发送外协需要按工序选公司）
# ============================================================
read_router = APIRouter(
    prefix="/outsource-companies",
    tags=["外协管理(读)"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER,
            UserRole.CLERK,
            UserRole.CNC_PROGRAMMER,
            UserRole.INSPECTOR,
        ))
    ],
)


@read_router.get(
    "",
    response_model=OutsourceCompanyListOut,
    summary="外协公司列表（MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR）",
)
async def list_outsource_companies(
    name_like: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> OutsourceCompanyListOut:
    return await svc.list_companies(OutsourceCompanyListQuery(
        name_like=name_like,
        is_active=is_active,
        limit=limit,
        offset=offset,
    ))


@read_router.get(
    "/by-process/{process_id}",
    response_model=list[OutsourceCompanyOut],
    summary=(
        "按工序反查能做该 OUTSOURCE 工序的活跃公司"
        "（MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR；发送外协对话框用）"
    ),
)
async def list_companies_by_process(
    process_id: int,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> list[OutsourceCompanyOut]:
    return await svc.list_companies_for_process(process_id)


@read_router.get(
    "/{company_id}",
    response_model=OutsourceCompanyWithProcessesOut,
    summary="外协公司详情（含映射的工序，MANAGER / CLERK / CNC_PROGRAMMER / INSPECTOR）",
)
async def get_outsource_company(
    company_id: str,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> OutsourceCompanyWithProcessesOut:
    return await svc.get_company(company_id)


# ============================================================
# 写路由：MANAGER + CLERK
# ============================================================
write_router = APIRouter(
    prefix="/outsource-companies",
    tags=["外协管理(写)"],
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))
    ],
)


@read_router.get(
    "/{company_id}/sent-parts",
    response_model=OutsourceSentPartListOut,
    summary=(
        "外协对账一览（2026-07-29 重写）：基于 t_outsource_quote 统一事实表，"
        "列出所有送给该公司的零件 + 发送/回收时间 + 单价/数量/对账标记"
        "（MANAGER / CLERK）。"
    ),
)
async def list_company_sent_parts(
    company_id: str,
    keyword: str | None = Query(default=None),
    sent_from: datetime | None = Query(default=None, description="发送时间起点（含）"),
    sent_to: datetime | None = Query(default=None, description="发送时间终点（含）"),
    received_from: datetime | None = Query(default=None, description="回收时间起点（含）"),
    received_to: datetime | None = Query(default=None, description="回收时间终点（含）"),
    sort_by: OutsourceSentPartSortKey = Query(
        default=OutsourceSentPartSortKey.SENT_AT,
        description="排序字段：PRICE / SENT_AT / RECEIVED_AT",
    ),
    sort_dir: SortDir = Query(default=SortDir.DESC, description="排序方向 ASC / DESC"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> OutsourceSentPartListOut:
    return await svc.list_sent_parts(
        company_id=company_id,
        query=OutsourceSentPartListQuery(
            keyword=keyword,
            sent_from=sent_from, sent_to=sent_to,
            received_from=received_from, received_to=received_to,
            sort_by=sort_by, sort_dir=sort_dir,
            limit=limit, offset=offset,
        ),
    )


@write_router.post(
    "",
    response_model=OutsourceCompanyWithProcessesOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增外协公司（可选一并写入工序能力，MANAGER / CLERK）",
)
async def create_outsource_company(
    payload: OutsourceCompanyCreateRequest,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> OutsourceCompanyWithProcessesOut:
    return await svc.create_company(payload)


@write_router.post(
    "/{company_id}/update",
    response_model=OutsourceCompanyWithProcessesOut,
    summary="更新外协公司基础字段（MANAGER / CLERK）",
)
async def update_outsource_company(
    company_id: str,
    payload: OutsourceCompanyUpdateRequest,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> OutsourceCompanyWithProcessesOut:
    return await svc.update_company(company_id, payload)


@write_router.post(
    "/{company_id}/soft-delete",
    summary="软删外协公司（仍有工序映射时拒绝，MANAGER / CLERK）",
)
async def soft_delete_outsource_company(
    company_id: str,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> dict:
    await svc.soft_delete_company(company_id)
    return {"ok": True}


@write_router.post(
    "/{company_id}/processes",
    response_model=OutsourceCompanyWithProcessesOut,
    summary="整体替换外协公司的工序能力清单（MANAGER / CLERK）",
)
async def set_outsource_company_processes(
    company_id: str,
    payload: SetOutsourceCompanyProcessRequest,
    svc: OutsourceCompanyService = Depends(get_outsource_company_service),
) -> OutsourceCompanyWithProcessesOut:
    return await svc.set_outsource_company_processes(company_id, payload)