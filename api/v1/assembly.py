"""装配体 API。

路径前缀：`/assemblies`
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status as http_status

from api.deps import get_assembly_service, get_drawing_service
from schema.assembly import (
    AssemblyCreateRequest,
    AssemblyCreateResult,
    AssemblyDetail,
    AssemblyListOut,
    AssemblyListQuery,
)
from schema.drawing import DrawingFileOut
from service.assembly import AssemblyService
from service.drawing import DrawingService

# 装配体自身的 CRUD
router = APIRouter(prefix="/assemblies", tags=["装配体管理"])


@router.get(
    "",
    response_model=AssemblyListOut,
    summary="分页查询装配体列表",
)
async def list_assemblies(
    customer_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    is_urgent: bool | None = Query(default=None),
    drawing_no_like: str | None = Query(default=None),
    name_like: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyListOut:
    return await svc.list_assemblies(
        AssemblyListQuery(
            customer_id=customer_id,
            status=status,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=AssemblyCreateResult,
    status_code=http_status.HTTP_201_CREATED,
    summary="创建装配件 + 上传总装 PDF + 生成子零件",
)
async def create_assembly(
    data: str = Form(..., description="AssemblyCreateRequest 的 JSON 字符串"),
    file: UploadFile = File(..., description="总装 PDF（必填，第一页是总装图）"),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyCreateResult:
    payload = AssemblyCreateRequest.model_validate_json(data)
    pdf_bytes = await file.read()
    return await svc.create_assembly(
        payload, pdf_bytes=pdf_bytes, pdf_filename=file.filename or "assembly.pdf"
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
    summary="级联软删装配件 + 子件 + 文件（COS 文件异步清理）",
)
async def soft_delete_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> dict:
    await svc.soft_delete_assembly(assembly_id)
    return {"ok": True}


# 装配件级文件管理（multipart 子路由挂同一个 prefix）
file_router = APIRouter(prefix="/assemblies", tags=["装配体管理"])


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
