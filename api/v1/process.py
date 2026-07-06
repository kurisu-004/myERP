"""工序 (Process) 路由。"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_process_service
from core.permission import require_role, require_roles
from model.enums import ProcessCategory, UserRole
from schema.process import (
    ProcessCreateRequest,
    ProcessListOut,
    ProcessListQuery,
    ProcessOut,
    ProcessUpdateRequest,
)
from service import ProcessService

# 写操作 MANAGER-only；读开放给 MANAGER + CLERK + CNC_PROGRAMMER（下拉用）。
router = APIRouter(prefix="/processes", tags=["工序管理"])
_mgr_dep = [Depends(require_role(UserRole.MANAGER))]
_read_dep = [
    Depends(require_roles(
        UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
    ))
]


@router.get(
    "",
    response_model=ProcessListOut,
    summary="工序列表（MANAGER / CLERK / CNC_PROGRAMMER）",
    dependencies=_read_dep,
)
async def list_processes(
    code_like: str | None = Query(default=None),
    category: ProcessCategory | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: ProcessService = Depends(get_process_service),
) -> ProcessListOut:
    return await svc.list_processes(
        ProcessListQuery(
            code_like=code_like,
            category=category,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=ProcessOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增工序",
    dependencies=_mgr_dep,
)
async def create_process(
    payload: ProcessCreateRequest,
    svc: ProcessService = Depends(get_process_service),
) -> ProcessOut:
    return await svc.create_process(payload)


@router.get(
    "/{process_id}",
    response_model=ProcessOut,
    summary="工序详情（MANAGER / CLERK / CNC_PROGRAMMER）",
    dependencies=_read_dep,
)
async def get_process(
    process_id: int,
    svc: ProcessService = Depends(get_process_service),
) -> ProcessOut:
    return await svc.get_process(process_id)


@router.post(
    "/{process_id}/update",
    response_model=ProcessOut,
    summary="更新工序字段（code 不可改）",
    dependencies=_mgr_dep,
)
async def update_process(
    process_id: int,
    payload: ProcessUpdateRequest,
    svc: ProcessService = Depends(get_process_service),
) -> ProcessOut:
    return await svc.update_process(process_id, payload)


@router.post(
    "/{process_id}/soft-delete",
    summary="软删工序（被引用时拒绝）",
    dependencies=_mgr_dep,
)
async def soft_delete_process(
    process_id: int,
    svc: ProcessService = Depends(get_process_service),
) -> dict:
    await svc.soft_delete_process(process_id)
    return {"ok": True}