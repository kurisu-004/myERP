"""货架管理端点（MANAGER-only）。

货架本身不带账号；账号与货架的多对多关系通过 t_user_role 维护，
见 /api/v1/users/{user_id}/roles。
"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_shelf_service
from core.permission import require_role
from model.enums import ShelfZone, UserRole
from schema.shelf import (
    ShelfCreateRequest,
    ShelfListOut,
    ShelfListQuery,
    ShelfOut,
    ShelfUpdateRequest,
)
from service.shelf import ShelfService


router = APIRouter(
    prefix="/shelves",
    tags=["货架管理"],
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)


@router.get("", response_model=ShelfListOut, summary="货架列表")
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


@router.post(
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


@router.get("/{shelf_id}", response_model=ShelfOut, summary="货架详情")
async def get_shelf(
    shelf_id: int,
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfOut:
    return await svc.get_shelf(shelf_id)


@router.post(
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


@router.post(
    "/{shelf_id}/deactivate",
    response_model=ShelfOut,
    summary="软删货架（有 IN_PROCESS/INSPECTION 零件时拒）",
)
async def deactivate_shelf(
    shelf_id: int,
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfOut:
    return await svc.soft_delete_shelf(shelf_id)
