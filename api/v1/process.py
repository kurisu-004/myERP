"""工序 (Process) 路由。

路由结构（与 /customers、/shelves、/work-types 一致的 read/write 双 router 拆分）：
- **读端点**（GET）：MANAGER + CLERK + CNC_PROGRAMMER + SHELF_ACCOUNT。
  文员 / 编程员 / 共享 HMI 扫码台（下拉用）都要拉工序列表。
- **写端点**（POST 创建 / 更新 / 软删）：MANAGER-only。
  工序是组织结构资源，只允许管理员改动；其他角色只读使用。
"""
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

# ============================================================
# 读路由：MANAGER + CLERK + CNC_PROGRAMMER + SHELF_ACCOUNT
# （SHELF_ACCOUNT 在 2026-07-10 加入：共享 HMI 扫码台需要拉工序下拉）
# ============================================================
read_router = APIRouter(
    prefix="/processes",
    tags=["工序管理(读)"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER,
            UserRole.CLERK,
            UserRole.CNC_PROGRAMMER,
            UserRole.SHELF_ACCOUNT,
        ))
    ],
)

_mgr_dep = [Depends(require_role(UserRole.MANAGER))]


@read_router.get(
    "",
    response_model=ProcessListOut,
    summary="工序列表（MANAGER / CLERK / CNC_PROGRAMMER / SHELF_ACCOUNT）",
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


@read_router.get(
    "/{process_id}",
    response_model=ProcessOut,
    summary="工序详情（MANAGER / CLERK / CNC_PROGRAMMER / SHELF_ACCOUNT）",
)
async def get_process(
    process_id: int,
    svc: ProcessService = Depends(get_process_service),
) -> ProcessOut:
    return await svc.get_process(process_id)


# ============================================================
# 写路由：MANAGER-only
# ============================================================
write_router = APIRouter(
    prefix="/processes",
    tags=["工序管理(写)"],
    dependencies=_mgr_dep,
)


@write_router.post(
    "",
    response_model=ProcessOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增工序",
)
async def create_process(
    payload: ProcessCreateRequest,
    svc: ProcessService = Depends(get_process_service),
) -> ProcessOut:
    return await svc.create_process(payload)


@write_router.post(
    "/{process_id}/update",
    response_model=ProcessOut,
    summary="更新工序字段",
)
async def update_process(
    process_id: int,
    payload: ProcessUpdateRequest,
    svc: ProcessService = Depends(get_process_service),
) -> ProcessOut:
    return await svc.update_process(process_id, payload)


@write_router.post(
    "/{process_id}/soft-delete",
    summary="软删工序（被引用时拒绝）",
)
async def soft_delete_process(
    process_id: int,
    svc: ProcessService = Depends(get_process_service),
) -> dict:
    await svc.soft_delete_process(process_id)
    return {"ok": True}