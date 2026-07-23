"""工种 (WorkType) + 工种↔工序映射 路由。

路由结构（与 /customers、/shelves 一致的 read/write 双 router 拆分）：
- **读端点**（GET）：MANAGER + CLERK + CNC_PROGRAMMER + SHELF_ACCOUNT + INSPECTOR。
  文员 / 编程员 / 共享 HMI 扫码台（下拉用）都要拉工种 / 映射列表。
  2026-07-23 INSPECTOR 加入：送货扫码台 `DispatchBadgeGate` 解析扫描到的工人工种码
  会调 `GET /work-types?limit=200`，需要放行。
- **写端点**（POST 创建 / 更新 / 软删）：MANAGER-only。
  工种是组织结构资源，只允许管理员改动；其他角色只读使用。

注：`GET /{work_type_id}/processes`（工种映射列表）原挂在写 router 上，
2026-07-10 SHELF_ACCOUNT 共享 HMI 接入时一并迁到读 router：
CLERK / CNC / HMI 都要查工种映射来过滤可领件。
"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import (
    get_work_type_process_service,
    get_work_type_service,
)
from core.permission import require_role, require_roles
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

# ============================================================
# 读路由：MANAGER + CLERK + CNC_PROGRAMMER + SHELF_ACCOUNT + INSPECTOR
# （SHELF_ACCOUNT 在 2026-07-10 加入：共享 HMI 扫码台需要拉工种 +
#  工序映射列表来过滤 PICK_UP 候选；
#  2026-07-23 加入 INSPECTOR：送货扫码台 DispatchBadgeGate 解析扫描到的工人工种码
#  会调 GET /work-types?limit=200）
# ============================================================
read_router = APIRouter(
    prefix="/work-types",
    tags=["工种管理(读)"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER,
            UserRole.CLERK,
            UserRole.CNC_PROGRAMMER,
            UserRole.SHELF_ACCOUNT,
            UserRole.INSPECTOR,
        ))
    ],
)

_mgr_dep = [Depends(require_role(UserRole.MANAGER))]


@read_router.get(
    "",
    response_model=WorkTypeListOut,
    summary="工种列表（MANAGER / CLERK / CNC_PROGRAMMER / SHELF_ACCOUNT / INSPECTOR）",
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


@read_router.get(
    "/{work_type_id}",
    response_model=WorkTypeOut,
    summary="工种详情（MANAGER / CLERK / CNC_PROGRAMMER / SHELF_ACCOUNT / INSPECTOR）",
)
async def get_work_type(
    work_type_id: int,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeOut:
    return await svc.get_work_type(work_type_id)


@read_router.get(
    "/{work_type_id}/processes",
    response_model=WorkTypeWithProcessesOut,
    summary=(
        "工种当前映射的工序列表（含 sort_order）"
        "（MANAGER / CLERK / CNC_PROGRAMMER / SHELF_ACCOUNT / INSPECTOR）"
    ),
)
async def list_work_type_processes(
    work_type_id: int,
    svc: WorkTypeProcessService = Depends(get_work_type_process_service),
) -> WorkTypeWithProcessesOut:
    return await svc.list_for_work_type(work_type_id)


# ============================================================
# 写路由：MANAGER-only
# ============================================================
write_router = APIRouter(
    prefix="/work-types",
    tags=["工种管理(写)"],
    dependencies=_mgr_dep,
)


@write_router.post(
    "",
    response_model=WorkTypeOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增工种",
)
async def create_work_type(
    payload: WorkTypeCreateRequest,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeOut:
    return await svc.create_work_type(payload)


@write_router.post(
    "/{work_type_id}/update",
    response_model=WorkTypeOut,
    summary="更新工种字段（code 不可改）",
)
async def update_work_type(
    work_type_id: int,
    payload: WorkTypeUpdateRequest,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> WorkTypeOut:
    return await svc.update_work_type(work_type_id, payload)


@write_router.post(
    "/{work_type_id}/soft-delete",
    summary="软删工种（被引用时拒绝）",
)
async def soft_delete_work_type(
    work_type_id: int,
    svc: WorkTypeService = Depends(get_work_type_service),
) -> dict:
    await svc.soft_delete_work_type(work_type_id)
    return {"ok": True}


@write_router.post(
    "/{work_type_id}/processes",
    response_model=WorkTypeWithProcessesOut,
    summary="整体替换工种的工序映射",
)
async def set_work_type_processes(
    work_type_id: int,
    payload: SetWorkTypeProcessRequest,
    svc: WorkTypeProcessService = Depends(get_work_type_process_service),
) -> WorkTypeWithProcessesOut:
    return await svc.set_for_work_type(work_type_id, payload)