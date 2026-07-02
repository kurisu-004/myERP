from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_worker_service
from core.permission import require_auth, require_role
from model.enums import UserRole
from schema.worker import (
    BadgeVerifyRequest,
    WorkerCreateRequest,
    WorkerListOut,
    WorkerListQuery,
    WorkerOut,
    WorkerUpdateRequest,
)
from service import WorkerService

# 不在 router 级挂 MANAGER 守卫：扫码台 verify-badge 需要让 SHELF_ACCOUNT 也能调。
# 所有需要 MANAGER 的端点显式挂 _mgr_dep。
router = APIRouter(
    prefix="/workers",
    tags=["工人管理"],
)
_mgr_dep = [Depends(require_role(UserRole.MANAGER))]


@router.post(
    "/verify-badge",
    response_model=WorkerOut,
    summary="扫码台按工牌码定位工人（任意已登录用户；车间扫码台用）",
    description=(
        "POST body 仅含 badge_code；后端单点查询，不返工人列表。"
        "避免扫描工牌时拉一次全量工人导致信息泄露与越权。"
        "已停用工人返回 400 BIZ_WORKER_INACTIVE。"
    ),
    dependencies=[Depends(require_auth())],
)
async def verify_badge(
    payload: BadgeVerifyRequest,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.verify_badge(payload.badge_code)


@router.get(
    "",
    response_model=WorkerListOut,
    summary="工人列表",
    dependencies=_mgr_dep,
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
    dependencies=_mgr_dep,
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
    dependencies=_mgr_dep,
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
    dependencies=_mgr_dep,
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
    dependencies=_mgr_dep,
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
    dependencies=_mgr_dep,
)
async def reactivate_worker(
    worker_id: int,
    svc: WorkerService = Depends(get_worker_service),
) -> WorkerOut:
    return await svc.reactivate(worker_id)