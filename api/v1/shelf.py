"""货架管理端点。

路由结构（与 /customers、/work-types 一致的 read/write 双 router 拆分）：
- **读端点**（GET）：MANAGER + CLERK + CNC_PROGRAMMER + SHELF_ACCOUNT + INSPECTOR。
  文员下发零件 / 编程员下达 / 扫码台取件 / 用户管理下拉等业务页都要拉货架列表；
  INSPECTOR 在外协发送/接收 + 待品检品检打回弹框需要货架下拉（PR-I 2026-07-20）。
- **写端点**（POST 创建 / 更新 / 软删）：MANAGER-only。
  货架是组织结构资源，只允许管理员改动；CLERK / CNC_PROGRAMMER / INSPECTOR
  通过现有菜单（订单管理、待编程一览、扫码台、待品检、外协发送接收）只读使用。

货架本身不带账号；账号与货架的多对多关系通过 t_user_role 维护，
见 /api/v1/users/{user_id}/roles。

2026-07-13 SHELF_ACCOUNT 多货架改造：
- /shelves/for-return 增加 user scope 收口（scoped SHELF_ACCOUNT 仅看到自己绑定的架）
- 新增 /shelves/for-inspection：INSPECT 流程 picker，按 user scope 收口
"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_shelf_process_service, get_shelf_service
from core.permission import (
    CurrentUser,
    get_current_user,
    require_auth,
    require_role,
    require_roles,
)
from model.enums import ShelfZone, UserRole
from schema.shelf import (
    ShelfCreateRequest,
    ShelfForReturnListOut,
    ShelfListOut,
    ShelfListQuery,
    ShelfOut,
    ShelfProcessMappingsOut,
    ShelfUpdateRequest,
)
from schema.shelf_process import SetShelfProcessRequest, ShelfWithProcessesOut
from service._id_parse import parse_snowflake_id
from service.shelf import ShelfService
from service.shelf_process import ShelfProcessService

# ============================================================
# 读路由：MANAGER + CLERK + CNC_PROGRAMMER + SHELF_ACCOUNT + INSPECTOR
# （SHELF_ACCOUNT 在 2026-07-10 加入：共享 HMI 扫码台需要拉货架详情来渲染
#  PICK_UP / RETURN 卡片网格；
#  INSPECTOR 在 PR-I 2026-07-20 加入：外协发送/接收 + 待品检品检打回弹框需要货架下拉；
#  写入仍由 write_router MANAGER-only 控制）
# ============================================================
read_router = APIRouter(
    prefix="/shelves",
    tags=["货架管理(读)"],
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
# 共享 HMI picker 路由（任意已登录用户可调，含 SHELF_ACCOUNT 共享账号）
# 2026-07-10：RETURN 流程卡片网格 picker 数据源
# ============================================================
picker_router = APIRouter(
    prefix="/shelves",
    tags=["货架管理(HMI picker)"],
    dependencies=[Depends(require_auth())],
)


@picker_router.get(
    "/for-return",
    response_model=ShelfForReturnListOut,
    summary=(
        "共享 HMI RETURN 卡片网格 picker：列出可放目标货架 + 系统推荐。"
        "候选 = active PRODUCTION ∩ 映射了 next_process_id；"
        "按 current_load ASC 排序，top-1 标 is_recommended。"
        "2026-07-13 起 scoped SHELF_ACCOUNT 仅看到自己绑定的架；"
        "MANAGER / wildcard 不收口。"
    ),
)
async def list_shelves_for_return(
    next_process_id: str = Query(
        ..., description="目标工序 id（雪花 ID 字符串，避免 JS Number 精度丢失）",
    ),
    user: CurrentUser = Depends(get_current_user),
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfForReturnListOut:
    # 入参是雪花 ID 字符串，转回 int（CLAUDE.md §3）。
    next_process_id_int = parse_snowflake_id(
        next_process_id, field_name="next_process_id",
    )
    return await svc.list_for_return(next_process_id_int, user=user)


@picker_router.get(
    "/for-inspection",
    response_model=ShelfForReturnListOut,
    summary=(
        "共享 HMI INSPECT 卡片网格 picker（2026-07-13）：列出 HMI 货架范围内 "
        "active INSPECTION 货架 + 系统推荐。无 next_process_id 限制；"
        "scoped SHELF_ACCOUNT 仅看到自己绑定的品检架；"
        "MANAGER / wildcard 不收口。"
    ),
)
async def list_shelves_for_inspection(
    user: CurrentUser = Depends(get_current_user),
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfForReturnListOut:
    return await svc.list_for_inspection(user=user)


@picker_router.get(
    "/processes",
    response_model=ShelfProcessMappingsOut,
    summary=(
        "批量返回所有 active 货架的工序 id 列表（2026-07-17）。"
        "给前端 useShelfProcessFilter composable 一次性消费，"
        "避免弹窗打开时 N+1 次 /shelves/{id}/processes 调用。"
        "空映射的货架不出现在 items 中。"
    ),
)
async def list_shelves_process_mappings(
    svc: ShelfService = Depends(get_shelf_service),
) -> ShelfProcessMappingsOut:
    return await svc.list_all_process_mappings()


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
