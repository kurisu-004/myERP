from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_part_service
from core.permission import (
    CurrentUser,
    require_auth,
    require_role,
    require_shelf_account_from_body,
)
from model.enums import UserRole
from schema.part import (
    PartBatchCreateRequest,
    PartBatchCreateResult,
    PartCreateRequest,
    PartEventOut,
    PartListOut,
    PartListQuery,
    PartOut,
    PartPickUpRequest,
    PartScanRequest,
    PartStatusChangeRequest,
    PlaceOnShelfRequest,
)
from service import PartService

router = APIRouter(prefix="/parts", tags=["零件管理"])


# ============================================================
# MANAGER-only 路由
# ============================================================
_mgr_dep = [Depends(require_role(UserRole.MANAGER))]


@router.get(
    "",
    response_model=PartListOut,
    summary="分页查询零件列表（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def list_parts(
    customer_id: int | None = Query(default=None, description="客户 id（二级叶子节点）"),
    status: str | None = Query(default=None, description="订单状态"),
    is_urgent: bool | None = Query(default=None, description="是否加急"),
    drawing_no_like: str | None = Query(default=None, description="图号模糊匹配"),
    name_like: str | None = Query(default=None, description="名称模糊匹配"),
    sort_by: str = Query(default="PLANNED_DELIVERY_DATE", description="排序字段"),
    sort_dir: str = Query(default="ASC", description="排序方向"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: PartService = Depends(get_part_service),
) -> PartListOut:
    from model.enums import PartSortKey, PartStatus, SortDir

    return await svc.list_parts(
        PartListQuery(
            customer_id=customer_id,
            status=PartStatus(status) if status else None,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            sort_by=PartSortKey(sort_by),
            sort_dir=SortDir(sort_dir),
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=PartOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增 PENDING 零件（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def create_part(
    payload: PartCreateRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.create_part(payload)


@router.post(
    "/batch",
    response_model=PartBatchCreateResult,
    summary="批量新增零件（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def create_parts_batch(
    payload: PartBatchCreateRequest,
    svc: PartService = Depends(get_part_service),
) -> PartBatchCreateResult:
    return await svc.create_parts_batch(payload)


@router.get(
    "/{part_id}",
    response_model=PartOut,
    summary="零件详情（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def get_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.get_part(part_id)


@router.post(
    "/{part_id}/change-status",
    response_model=PartOut,
    summary="修改零件状态（MANAGER-only；COMPLETED/CANCELLED 会释放序列号）",
    dependencies=_mgr_dep,
)
async def change_part_status(
    part_id: int,
    payload: PartStatusChangeRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.change_status(part_id, payload)


@router.post(
    "/{part_id}/soft-delete",
    summary="软删零件（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def soft_delete_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> dict:
    await svc.soft_delete_part(part_id)
    return {"ok": True}


@router.post(
    "/{part_id}/place-on-shelf",
    response_model=PartOut,
    summary="PENDING → IN_PROCESS：把零件放到生产货架（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def place_part_on_shelf(
    part_id: int,
    payload: PlaceOnShelfRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.place_on_shelf(part_id, payload)


@router.get(
    "/{part_id}/events",
    response_model=list[PartEventOut],
    summary="该零件的全生命周期事件流（MANAGER-only）",
    dependencies=_mgr_dep,
)
async def list_part_events(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> list[PartEventOut]:
    return await svc.list_events(part_id)


# ============================================================
# SHELF_ACCOUNT 路由（扫码台用）
# ============================================================
@router.post(
    "/pick-up",
    response_model=PartOut,
    summary="工人扫码领取（SHELF_ACCOUNT @ 该 shelf）：holder shelf → worker",
)
async def pick_up_part(
    payload: PartPickUpRequest,
    ctx: tuple[CurrentUser, int] = Depends(
        require_shelf_account_from_body("shelf_id")
    ),
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    _user, _shelf_id = ctx
    return await svc.pick_up_by_scan(payload)


@router.post(
    "/scan",
    response_model=PartOut,
    summary="工人扫图纸归还 / 送检（SHELF_ACCOUNT @ 该 shelf）",
)
async def scan_part(
    payload: PartScanRequest,
    ctx: tuple[CurrentUser, int] = Depends(
        require_shelf_account_from_body("shelf_id")
    ),
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    _user, _shelf_id = ctx
    return await svc.scan_event(payload)


# ============================================================
# 任意已登录用户可调（SHELF_ACCOUNT 扫码台按 serial 查零件用）
# ============================================================
@router.get(
    "/by-serial/{serial_no}",
    response_model=PartOut,
    summary="按序列号定位零件（任意已登录用户）",
    dependencies=[Depends(require_auth())],
)
async def get_part_by_serial(
    serial_no: str,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    from core.error_code import ErrCode
    from core.exception import BizError

    part = await svc.parts.get_by_serial(serial_no)
    if part is None:
        raise BizError(
            code=ErrCode.BIZ_PART_NOT_FOUND,
            message=f"serial {serial_no} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    items = await svc._to_out([part])
    return items[0]