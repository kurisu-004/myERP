"""图纸文件 API。

子件反查：`/parts/{part_id}/assembly`（见 assembly router）
子件文件：`/parts/{part_id}/files`
绘图管理：`/drawings/{file_id}/...`

权限模型（2026-07-06 调整）：
- 上传 / 删除：MANAGER + CLERK（文员日常操作）
- 列 / 预览 / 下载：任意已登录用户
  （包括编程员要下载图纸来写程序；后端代理下载无需暴露 COS URL）
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile, status as http_status
from fastapi.responses import Response

from api.deps import get_drawing_service
from core.permission import get_current_user, require_roles
from model.enums import UserRole
from schema.drawing import DrawingFileOut
from service.drawing import DrawingService


# 写侧守卫：上传 / 删除图纸文件 → MANAGER + CLERK。
_office_write_dep = [Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))]


# ---------- 子件文件 ----------
# router 级仅要求已登录；列接口无需 role guard，写接口在 route 级显式收紧。
child_file_router = APIRouter(
    prefix="/parts",
    tags=["零件管理"],
    dependencies=[Depends(get_current_user)],
)


@child_file_router.post(
    "/{part_id}/files",
    response_model=DrawingFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为子零件上传附加文件（STEP/DWG/DXF 等，MANAGER / CLERK）",
    dependencies=_office_write_dep,
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
    summary="列出子零件的所有文件（任意已登录用户）",
)
async def list_part_files(
    part_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> list[DrawingFileOut]:
    return await drawings.list_for_part(part_id)


# ---------- 文件级操作（不关心归属） ----------
# 列 / 预览 / 下载：任意已登录；删除：MANAGER + CLERK。
file_router = APIRouter(
    prefix="/drawings",
    tags=["图纸文件"],
    dependencies=[Depends(get_current_user)],
)


@file_router.get(
    "/{file_id}/download-url",
    summary="重新签发单文件临时下载 URL（任意已登录用户）",
)
async def get_download_url(
    file_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> dict:
    url = await drawings.get_download_url(file_id)
    return {"url": url}


@file_router.get(
    "/{file_id}/content",
    summary="通过后端代理获取文件内容（预览/下载，任意已登录用户）",
)
async def get_file_content(
    file_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
):
    data, content_type, filename = await drawings.get_file_content(file_id)
    encoded = quote(filename, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded}"},
    )


@file_router.post(
    "/{file_id}/delete",
    summary="软删文件（COS 对象异步清理，MANAGER / CLERK）",
    dependencies=_office_write_dep,
)
async def delete_file(
    file_id: int,
    drawings: DrawingService = Depends(get_drawing_service),
) -> dict:
    await drawings.delete_file(file_id)
    return {"ok": True}
