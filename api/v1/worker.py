from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_worker_service
from core.permission import require_role
from model.enums import UserRole
from schema.worker import (
    WorkerCreateRequest,
    WorkerListOut,
    WorkerListQuery,
    WorkerOut,
    WorkerUpdateRequest,
)
from service import WorkerService

router = APIRouter(
    prefix="/workers",
    tags=["工人管理"],
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)


@router.get(
    "",
    response_model=WorkerListOut,
    summary="工人列表",
)
async def list_workers(
    name_like: str | None = Query(default=None, description="姓名模糊匹配"),
    is_active: bool | None = Query(default=None, description="是否在职"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerListOut:
    return await svc.list_workers(
        WorkerListQuery(
            name_like=name_like,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=WorkerOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增工人（录入工牌码 + 姓名）",
)
async def create_worker(
    payload: WorkerCreateRequest,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.create_worker(payload)


@router.get(
    "/{worker_id}",
    response_model=WorkerOut,
    summary="工人详情",
)
async def get_worker(
    worker_id: int,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.get_worker(worker_id)


@router.post(
    "/{worker_id}/update",
    response_model=WorkerOut,
    summary="更新工人字段（仅传需要改的）",
)
async def update_worker(
    worker_id: int,
    payload: WorkerUpdateRequest,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.update_worker(worker_id, payload)


@router.post(
    "/{worker_id}/deactivate",
    response_model=WorkerOut,
    summary="停用工人（软删 + is_active=false）",
)
async def deactivate_worker(
    worker_id: int,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.deactivate(worker_id)


@router.post(
    "/{worker_id}/reactivate",
    response_model=WorkerOut,
    summary="重新启用工人",
)
async def reactivate_worker(
    worker_id: int,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.reactivate(worker_id)