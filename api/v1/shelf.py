"""货架管理端点。

路由结构（与 /customers、/work-types 一致的 read/write 双 router 拆分）：
- **读端点**（GET）：MANAGER + CLERK + CNC_PROGRAMMER。
  文员下发零件 / 编程员下达 / 扫码台取件 / 用户管理下拉等业务页都要拉货架列表。
- **写端点**（POST 创建 / 更新 / 软删）：MANAGER-only。
  货架是组织结构资源，只允许管理员改动；CLERK / CNC_PROGRAMMER 通过现有菜单
  （订单管理、待编程一览、扫码台）只读使用。

货架本身不带账号；账号与货架的多对多关系通过 t_user_role 维护，
见 /api/v1/users/{user_id}/roles。
"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_shelf_process_service, get_shelf_service
from core.permission import require_role, require_roles
from model.enums import ShelfZone, UserRole
from schema.shelf import (
    ShelfCreateRequest,
    ShelfListOut,
    ShelfListQuery,
    ShelfOut,
    ShelfUpdateRequest,
)
from schema.shelf_process import SetShelfProcessRequest, ShelfWithProcessesOut
from service.shelf import ShelfService
from service.shelf_process import ShelfProcessService

# ============================================================
# 读路由：MANAGER + CLERK + CNC_PROGRAMMER
# ============================================================
read_router = APIRouter(
    prefix="/shelves",
    tags=["货架管理(读)"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        ))
    ],
)


@read_router.get("", response_model=ShelfListOut, summary="货架列表")
async def list_shelves(
    zone: ShelfZone | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfListOut:
    return await svc.list_shelves(
        ShelfListQuery(zone=zone, is_active=is_active, limit=limit, offset=offset)
    )


@read_router.get("/{shelf_id}", response_model=ShelfOut, summary="货架详情")
async def get_shelf(
    shelf_id: int,
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfOut:
    return await svc.get_shelf(shelf_id)


@read_router.get(
    "/{shelf_id}/processes",
    response_model=ShelfWithProcessesOut,
    summary="货架当前映射的工序列表",
)
async def list_shelf_processes(
    shelf_id: int,
    svc: ShelfProcessService = Depends(get_shelf_process_service),
) -> ShelfWithProcessesOut:
    return await svc.list_for_shelf(shelf_id)


# ============================================================
# 写路由：MANAGER-only
# ============================================================
write_router = APIRouter(
    prefix="/shelves",
    tags=["货架管理(写)"],
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)


@write_router.post(
    "",
    response_model=ShelfOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="创建货架",
)
async def create_shelf(
    payload: ShelfCreateRequest,
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfOut:
    return await svc.create_shelf(payload)


@write_router.post(
    "/{shelf_id}/update",
    response_model=ShelfOut,
    summary="更新货架（partial）",
)
async def update_shelf(
    shelf_id: int,
    payload: ShelfUpdateRequest,
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfOut:
    return await svc.update_shelf(shelf_id, payload)


@write_router.post(
    "/{shelf_id}/deactivate",
    response_model=ShelfOut,
    summary="软删货架（有 IN_PROCESS/INSPECTION 零件时拒）",
)
async def deactivate_shelf(
    shelf_id: int,
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfOut:
    return await svc.soft_delete_shelf(shelf_id)


@write_router.post(
    "/{shelf_id}/processes",
    response_model=ShelfWithProcessesOut,
    summary="整体替换货架的工序映射",
)
async def set_shelf_processes(
    shelf_id: int,
    payload: SetShelfProcessRequest,
    svc: ShelfProcessService = Depends(get_shelf_process_service),
) -> ShelfWithProcessesOut:
    return await svc.set_for_shelf(shelf_id, payload)
