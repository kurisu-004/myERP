from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_part_service
from model.enums import PartSortKey, PartStatus, SortDir
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
)
from service import PartService

router = APIRouter(prefix="/parts", tags=["零件管理"])


@router.get(
    "",
    response_model=PartListOut,
    summary="分页查询零件列表（数据大屏用）",
)
async def list_parts(
    customer_id: int | None = Query(default=None, description="客户 id（二级叶子节点）"),
    status: PartStatus | None = Query(default=None, description="订单状态"),
    is_urgent: bool | None = Query(default=None, description="是否加急"),
    drawing_no_like: str | None = Query(default=None, description="图号模糊匹配"),
    name_like: str | None = Query(default=None, description="名称模糊匹配"),
    sort_by: PartSortKey = Query(
        default=PartSortKey.PLANNED_DELIVERY_DATE, description="排序字段"
    ),
    sort_dir: SortDir = Query(default=SortDir.ASC, description="排序方向"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: PartService = Depends(get_part_service),
) -> PartListOut:
    return await svc.list_parts(
        PartListQuery(
            customer_id=customer_id,
            status=status,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=PartOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增 PENDING 零件（系统自动分配序列号）",
)
async def create_part(
    payload: PartCreateRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.create_part(payload)


@router.post(
    "/batch",
    response_model=PartBatchCreateResult,
    summary="批量新增零件（Excel 导入用；任一 DB 错误回滚整个 batch）",
)
async def create_parts_batch(
    payload: PartBatchCreateRequest,
    svc: PartService = Depends(get_part_service),
) -> PartBatchCreateResult:
    return await svc.create_parts_batch(payload)


@router.get(
    "/{part_id}",
    response_model=PartOut,
    summary="零件详情",
)
async def get_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.get_part(part_id)


@router.post(
    "/{part_id}/change-status",
    response_model=PartOut,
    summary="修改零件状态（COMPLETED/CANCELLED 会释放序列号）",
)
async def change_part_status(
    part_id: int,
    payload: PartStatusChangeRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.change_status(part_id, payload)


@router.post(
    "/{part_id}/soft-delete",
    summary="软删零件",
)
async def soft_delete_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> dict:
    await svc.soft_delete_part(part_id)
    return {"ok": True}


@router.post(
    "/{part_id}/release",
    response_model=PartOut,
    summary='文员点击「开始生产」（PENDING → READY）',
)
async def release_part(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.release_to_floor(part_id)


@router.post(
    "/pick-up",
    response_model=PartOut,
    summary="工人扫码领取（图纸码 + 工牌码；READY → IN_PROCESS）",
)
async def pick_up_part(
    payload: PartPickUpRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.pick_up_by_scan(payload)


@router.post(
    "/scan",
    response_model=PartOut,
    summary="工人扫图纸归还/送检（event_type ∈ {RETURNED, INSPECTED}）",
)
async def scan_part(
    payload: PartScanRequest,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    return await svc.scan_event(payload)


@router.get(
    "/{part_id}/events",
    response_model=list[PartEventOut],
    summary="该零件的全生命周期事件流（按 created_at 升序）",
)
async def list_part_events(
    part_id: int,
    svc: PartService = Depends(get_part_service),
) -> list[PartEventOut]:
    return await svc.list_events(part_id)


@router.get(
    "/by-serial/{serial_no}",
    response_model=PartOut,
    summary="按序列号定位零件",
)
async def get_part_by_serial(
    serial_no: str,
    svc: PartService = Depends(get_part_service),
) -> PartOut:
    part = await svc.parts.get_by_serial(serial_no)
    if part is None:
        from core.error_code import ErrCode
        from core.exception import BizError

        raise BizError(
            code=ErrCode.BIZ_PART_NOT_FOUND,
            message=f"serial {serial_no} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    items = await svc._to_out([part])
    return items[0]