"""工种 (WorkType) + 工种↔工序映射 路由。"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import (
    get_work_type_process_service,
    get_work_type_service,
)
from core.permission import require_role
from model.enums import UserRole
from schema.work_type import (
    WorkTypeCreateRequest,
    WorkTypeListOut,
    WorkTypeListQuery,
    WorkTypeOut,
    WorkTypeUpdateRequest,
)
from schema.work_type_process import (
    SetWorkTypeProcessRequest,
    WorkTypeWithProcessesOut,
)
from service import WorkTypeProcessService, WorkTypeService

router = APIRouter(prefix="/work-types", tags=["工种管理"])
_mgr_dep = [Depends(require_role(UserRole.MANAGER))]


@router.get(
    "",
    response_model=WorkTypeListOut,
    summary="工种列表",
    dependencies=_mgr_dep,
)
async def list_work_types(
    code_like: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeListOut:
    return await svc.list_work_types(
        WorkTypeListQuery(code_like=code_like, limit=limit, offset=offset)
    )


@router.post(
    "",
    response_model=WorkTypeOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增工种",
    dependencies=_mgr_dep,
)
async def create_work_type(
    payload: WorkTypeCreateRequest,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeOut:
    return await svc.create_work_type(payload)


@router.get(
    "/{work_type_id}",
    response_model=WorkTypeOut,
    summary="工种详情",
    dependencies=_mgr_dep,
)
async def get_work_type(
    work_type_id: int,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeOut:
    return await svc.get_work_type(work_type_id)


@router.post(
    "/{work_type_id}/update",
    response_model=WorkTypeOut,
    summary="更新工种字段（code 不可改）",
    dependencies=_mgr_dep,
)
async def update_work_type(
    work_type_id: int,
    payload: WorkTypeUpdateRequest,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeOut:
    return await svc.update_work_type(work_type_id, payload)


@router.post(
    "/{work_type_id}/soft-delete",
    summary="软删工种（被引用时拒绝）",
    dependencies=_mgr_dep,
)
async def soft_delete_work_type(
    work_type_id: int,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> dict:
    await svc.soft_delete_work_type(work_type_id)
    return {"ok": True}


# ============================================================
# 工种↔工序映射（挂在 work-types 路由下）
# ============================================================
@router.get(
    "/{work_type_id}/processes",
    response_model=WorkTypeWithProcessesOut,
    summary="工种当前映射的工序列表（含 sort_order）",
    dependencies=_mgr_dep,
)
async def list_work_type_processes(
    work_type_id: int,
    svc: WorkTypeProcessService = Depends(get_work_type_process_service),
) -> WorkTypeWithProcessesOut:
    return await svc.list_for_work_type(work_type_id)


@router.post(
    "/{work_type_id}/processes",
    response_model=WorkTypeWithProcessesOut,
    summary="整体替换工种的工序映射",
    dependencies=_mgr_dep,
)
async def set_work_type_processes(
    work_type_id: int,
    payload: SetWorkTypeProcessRequest,
    svc: WorkTypeProcessService = Depends(get_work_type_process_service),
) -> WorkTypeWithProcessesOut:
    return await svc.set_for_work_type(work_type_id, payload)