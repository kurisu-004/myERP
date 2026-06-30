"""图纸文件 + 子件反查 API。

子件反查：`/parts/{part_id}/assembly`
子件文件：`/parts/{part_id}/files`
绘图管理：`/drawings/{file_id}/...`
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, status as http_status

from api.deps import get_assembly_service, get_drawing_service
from schema.assembly import AssemblyDetail
from schema.drawing import DrawingFileOut
from service.assembly import AssemblyService
from service.drawing import DrawingService


# ---------- 子件反查 ----------
child_router = APIRouter(prefix="/parts", tags=["零件管理"])


@child_router.get(
    "/{part_id}/assembly",
    response_model=AssemblyDetail,
    summary="从任意子零件反查所属装配件（装配件自身/兄弟/文件）",
)
async def get_assembly_for_child(
    part_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.get_assembly_for_child(part_id)


# ---------- 子件文件 ----------
child_file_router = APIRouter(prefix="/parts", tags=["零件管理"])


@child_file_router.post(
    "/{part_id}/files",
    response_model=DrawingFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为子零件上传附加文件（STEP/DWG/DXF 等）",
)
async def upload_part_file(
    part_id: int,
    file: UploadFile = File(...),
    drawings: DrawingService = Depends(get_drawing_service),
) -> DrawingFileOut:
    data = await file.read()
    return await drawings.upload_to_part(
        part_id,
        data=data,
        original_filename=file.filename or "file",
        content_type=file.content_type,
        page_index=None,
    )


@child_file_router.get(
    "/{part_id}/files",
    response_model=list[DrawingFileOut],
    summary="列出子零件的所有文件",
)
async def list_part_files(
    part_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> list[DrawingFileOut]:
    return await drawings.list_for_part(part_id)


# ---------- 文件级操作（不关心归属） ----------
file_router = APIRouter(prefix="/drawings", tags=["图纸文件"])


@file_router.get(
    "/{file_id}/download-url",
    summary="重新签发单文件临时下载 URL",
)
async def get_download_url(
    file_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> dict:
    url = await drawings.get_download_url(file_id)
    return {"url": url}


@file_router.post(
    "/{file_id}/delete",
    summary="软删文件（COS 对象异步清理）",
)
async def delete_file(
    file_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> dict:
    await drawings.delete_file(file_id)
    return {"ok": True}
