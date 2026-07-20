"""外协公司 (OutsourceCompany) API。

路由结构（与 /customers 一致的 read/write 双 router 拆分）：
- **读端点**（GET）：MANAGER + CLERK + CNC_PROGRAMMER
  编程员要拉公司列表做「按工序过滤候选公司」（CNC 也有发外协的可能）。
- **写端点**（POST 创建 / 更新 / 软删 / 维护工序）：MANAGER + CLERK
  与申请人类似，CLERK 也可维护外协公司基础信息 + 工序能力。

另外提供：
- `GET /outsource-companies/by-process/{process_id}`：按工序反查能做此工序的
  活跃公司，给发送外协对话框前端过滤候选用。
"""
from fastapi import APIRouter, Depends, status as http_status

from api.deps import get_outsource_company_service
from core.permission import require_roles
from model.enums import UserRole
from schema.outsource_company import (
    OutsourceCompanyCreateRequest,
    OutsourceCompanyListOut,
    OutsourceCompanyListQuery,
    OutsourceCompanyOut,
    OutsourceCompanyUpdateRequest,
    OutsourceCompanyWithProcessesOut,
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