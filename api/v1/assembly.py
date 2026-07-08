"""装配体 API。

路径前缀：`/assemblies`
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status as http_status

from api.deps import get_assembly_service, get_drawing_service
from core.permission import require_role, require_roles
from model.enums import UserRole
from schema.assembly import (
    AddAssemblyChildRequest,
    AssemblyCreateRequest,
    AssemblyCreateResult,
    AssemblyDetail,
    AssemblyListOut,
    AssemblyListQuery,
)
from schema.drawing import DrawingFileOut
from service.assembly import AssemblyService
from service.drawing import DrawingService

# 装配体自身的 CRUD（MANAGER + CLERK：文员也能建/查/编辑装配体）
router = APIRouter(
    prefix="/assemblies",
    tags=["装配体管理"],
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))
    ],
)


@router.get(
    "",
    response_model=AssemblyListOut,
    summary="分页查询装配体列表",
)
async def list_assemblies(
    customer_id: str | None = Query(default=None, description="客户 id（雪花 ID 字符串）"),
    status: str | None = Query(default=None),
    is_urgent: bool | None = Query(default=None),
    drawing_no_like: str | None = Query(default=None),
    name_like: str | None = Query(default=None),
    sort_by: str = Query(
        default="PLANNED_DELIVERY_DATE",
        description="排序字段：PLANNED_DELIVERY_DATE / REQUEST_DATE / CREATED_AT / SERIAL_NO / DRAWING_NO / NAME",
    ),
    sort_dir: str = Query(default="ASC", description="排序方向：ASC / DESC"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyListOut:
    from repository.assembly import AssemblySortKey
    from model.enums import SortDir

    return await svc.list_assemblies(
        AssemblyListQuery(
            customer_id=customer_id,
            status=status,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            sort_by=AssemblySortKey(sort_by),
            sort_dir=SortDir(sort_dir),
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=AssemblyCreateResult,
    status_code=http_status.HTTP_201_CREATED,
    summary="创建装配件（可同时上传总装 PDF + 生成子零件；也可先建空装配体再到详情页补充）",
)
async def create_assembly(
    data: str = Form(..., description="AssemblyCreateRequest 的 JSON 字符串"),
    file: UploadFile | None = File(default=None, description="总装 PDF（可选；不传则创建空装配体）"),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyCreateResult:
    payload = AssemblyCreateRequest.model_validate_json(data)
    pdf_bytes = await file.read() if file is not None else b""
    return await svc.create_assembly(
        payload,
        pdf_bytes=pdf_bytes if pdf_bytes else None,
        pdf_filename=file.filename if file is not None else None,
    )


@router.get(
    "/{assembly_id}",
    response_model=AssemblyDetail,
    summary="装配件详情（自身 + 子件 + 文件）",
)
async def get_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.get_assembly_detail(assembly_id)


@router.post(
    "/{assembly_id}/soft-delete",
    summary="级联软删装配件 + 子件 + 文件（COS 文件异步清理，MANAGER-only）",
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)
async def soft_delete_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> dict:
    await svc.soft_delete_assembly(assembly_id)
    return {"ok": True}


@router.post(
    "/{assembly_id}/cancel",
    response_model=AssemblyDetail,
    summary="取消装配体，级联取消所有非终态子件",
)
async def cancel_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.cancel_assembly(assembly_id)


@router.post(
    "/{assembly_id}/upload-pdf",
    response_model=AssemblyDetail,
    status_code=http_status.HTTP_201_CREATED,
    summary="详情页上传总装 PDF：拆页 → page 1 = master + page 2..N = 自动创建子件",
)
async def upload_assembly_pdf(
    assembly_id: int,
    file: UploadFile = File(..., description="总装 PDF（必填，第一页是总装图）"),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    pdf_bytes = await file.read()
    return await svc.upload_total_pdf(
        assembly_id,
        pdf_bytes=pdf_bytes,
        pdf_filename=file.filename or "assembly.pdf",
    )


@router.post(
    "/{assembly_id}/children",
    response_model=dict,  # PartOut（避免循环引用；前端用 PartListItem 接收）
    status_code=http_status.HTTP_201_CREATED,
    summary="详情页添加单个子件（无 PDF；如需 PDF 走 POST /parts/{id}/files）",
)
async def add_assembly_child(
    assembly_id: int,
    payload: AddAssemblyChildRequest,
    svc: AssemblyService = Depends(get_assembly_service),
) -> dict:
    child_out = await svc.add_child(assembly_id, payload)
    return child_out.model_dump(mode="json")


# ---------- 子件反查（MANAGER + CLERK + CNC_PROGRAMMER） ----------
child_router = APIRouter(
    prefix="/parts",
    tags=["零件管理"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        ))
    ],
)


@child_router.get(
    "/{part_id}/assembly",
    response_model=AssemblyDetail,
    summary="从任意子零件反查所属装配件（装配件自身/兄弟/文件）",
)
async def get_assembly_for_child(
    part_id: str,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.get_assembly_for_child(part_id)


# 装配件级文件管理（MANAGER + CLERK + CNC_PROGRAMMER 读；上传仅 MANAGER+CLERK）
file_router = APIRouter(
    prefix="/assemblies",
    tags=["装配体管理"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        ))
    ],
)


@file_router.post(
    "/{assembly_id}/files",
    response_model=DrawingFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为装配件上传附加文件（STEP/DWG/DXF 等）",
)
async def upload_assembly_file(
    assembly_id: int,
    file: UploadFile = File(...),
    drawings: DrawingService = Depends(get_drawing_service),
) -> DrawingFileOut:
    data = await file.read()
    return await drawings.upload_to_assembly(
        assembly_id,
        data=data,
        original_filename=file.filename or "file",
        content_type=file.content_type,
        page_index=None,
    )


@file_router.get(
    "/{assembly_id}/files",
    response_model=list[DrawingFileOut],
    summary="装配件下的所有文件（含挂在子件上的）",
)
async def list_assembly_files(
    assembly_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> list[DrawingFileOut]:
    return await drawings.list_for_assembly(assembly_id)