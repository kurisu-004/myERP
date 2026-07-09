"""CNC 文件 API（2026-07-10 起：G 代码 + 设定单，合并为单 router）。

端点：
- POST /parts/{part_id}/cnc-programs   MANAGER + CNC_PROGRAMMER   kind=G_CODE
- POST /parts/{part_id}/setup-sheets   MANAGER + CNC_PROGRAMMER   kind=SETUP_SHEET (PDF)
- GET  /parts/{part_id}/cnc-programs   任意已登录                (kind 可选过滤)
- GET  /parts/{part_id}/setup-sheets   任意已登录
- GET  /cnc-programs/{file_id}/...     任意已登录  (别名 → /files/{id}/...)
- POST /cnc-programs/{file_id}/delete  MANAGER + CNC_PROGRAMMER  (按 file.kind 派角色)
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile, status as http_status
from fastapi.responses import Response

from api.deps import get_part_file_repository, get_part_file_service
from core.permission import (
    get_current_user,
    require_part_file_role,
)
from model.enums import PartFileKind
from repository.part_file import PartFileRepository
from schema.part_file import PartFileOut
from service.part_file import PartFileService


# ---------- 子件 CNC 文件（按 part） ----------
child_cnc_router = APIRouter(
    prefix="/parts",
    tags=["CNC编程"],
    dependencies=[Depends(get_current_user)],
)


@child_cnc_router.post(
    "/{part_id}/cnc-programs",
    response_model=PartFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为零件上传 G 代码（MANAGER + CNC_PROGRAMMER；允许多版本）",
    dependencies=[Depends(require_part_file_role(PartFileKind.G_CODE))],
)
async def upload_part_cnc_program(
    part_id: int,
    file: UploadFile = File(...),
    svc: PartFileService = Depends(get_part_file_service),
) -> PartFileOut:
    data = await file.read()
    return await svc.upload(
        owner_id=part_id,
        kind=PartFileKind.G_CODE,
        data=data,
        original_filename=file.filename or "program.nc",
        content_type=file.content_type,
    )


@child_cnc_router.post(
    "/{part_id}/setup-sheets",
    response_model=PartFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为零件上传 CNC 设定单 PDF（MANAGER + CNC_PROGRAMMER；自动覆盖旧文件）",
    dependencies=[Depends(require_part_file_role(PartFileKind.SETUP_SHEET))],
)
async def upload_part_setup_sheet(
    part_id: int,
    file: UploadFile = File(...),
    svc: PartFileService = Depends(get_part_file_service),
) -> PartFileOut:
    data = await file.read()
    return await svc.upload(
        owner_id=part_id,
        kind=PartFileKind.SETUP_SHEET,
        data=data,
        original_filename=file.filename or "setup_sheet.pdf",
        content_type=file.content_type,
    )


@child_cnc_router.get(
    "/{part_id}/cnc-programs",
    response_model=list[PartFileOut],
    summary="列出零件的 G 代码文件（任意已登录；可指定 kind=G_CODE）",
)
async def list_part_cnc_programs(
    part_id: int,
    kind: str | None = "G_CODE",
    svc: PartFileService = Depends(get_part_file_service),
) -> list[PartFileOut]:
    return await svc.list_for_part(part_id, kind=kind)


@child_cnc_router.get(
    "/{part_id}/setup-sheets",
    response_model=list[PartFileOut],
    summary="列出零件的 CNC 设定单（任意已登录）",
)
async def list_part_setup_sheets(
    part_id: int,
    svc: PartFileService = Depends(get_part_file_service),
) -> list[PartFileOut]:
    return await svc.list_for_part(part_id, kind=PartFileKind.SETUP_SHEET)


# ---------- CNC 文件级操作（兼容旧 URL，作为别名 → /files/{id}/...） ----------
program_router = APIRouter(
    prefix="/cnc-programs",
    tags=["CNC编程"],
    dependencies=[Depends(get_current_user)],
)


@program_router.get(
    "/{file_id}/download-url",
    summary="[别名] 重新签发单文件临时下载 URL → /files/{id}/download-url",
)
async def get_cnc_download_url(
    file_id: int,
    svc: PartFileService = Depends(get_part_file_service),
) -> dict:
    url = await svc.get_download_url(file_id)
    return {"url": url}


@program_router.get(
    "/{file_id}/content",
    summary="[别名] 后端代理获取内容 → /files/{id}/content",
)
async def get_cnc_file_content(
    file_id: int,
    svc: PartFileService = Depends(get_part_file_service),
):
    data, content_type, filename = await svc.get_file_content(file_id)
    encoded = quote(filename, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded}"},
    )


@program_router.post(
    "/{file_id}/delete",
    summary="[别名] 软删文件 → /files/{id}/delete；按 file.kind 自动校验角色",
)
async def delete_cnc_program(
    file_id: int,
    files: PartFileRepository = Depends(get_part_file_repository),
    svc: PartFileService = Depends(get_part_file_service),
    user=Depends(get_current_user),
) -> dict:
    f = await files.get_by_id(file_id)
    if f is None:
        from core.error_code import ErrCode
        from core.exception import BizError
        raise BizError(
            code=ErrCode.BIZ_PART_FILE_NOT_FOUND,
            message=f"file {file_id} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    dep = require_part_file_role(PartFileKind(f.kind))
    await dep(user)
    await svc.delete_file(file_id)
    return {"ok": True}