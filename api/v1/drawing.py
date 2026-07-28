"""零件文件 API（2026-07-10 起：图纸 + 3D 模型，合并为单 router）。

端点：
- POST /parts/{part_id}/drawings         MANAGER + CLERK   kind=DRAWING
                                            (PDF + PNG/JPG/GIF/BMP/TIFF/WEBP/HEIC 共 9 种)
- POST /parts/{part_id}/3d-models        MANAGER + CLERK   kind=3D_MODEL
                                            (STEP/STP/IGES/IGS/STL/OBJ/3MF)
- POST /parts/{part_id}/cad-files        MANAGER + CLERK   kind=CAD_2D (DWG/DXF)
- GET  /parts/{part_id}/files            任意已登录       (kind 可选过滤)
- GET  /files/{file_id}/download-url     任意已登录
- GET  /files/{file_id}/content          任意已登录
- POST /files/{file_id}/delete           按 file.kind 自动派角色

注：装配体的总装图（kind=ASSEMBLY_MASTER）通过 `POST /assemblies` 创建流
产生，polymorphic part_id = assembly.id，**不**走这个 router。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, UploadFile, status as http_status
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


# ---------- 零件文件（按 part） ----------
part_file_router = APIRouter(
    prefix="/parts",
    tags=["零件文件"],
    dependencies=[Depends(get_current_user)],
)


@part_file_router.post(
    "/{part_id}/drawings",
    response_model=PartFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为零件上传图纸 PDF（MANAGER + CLERK；自动覆盖旧文件）",
    dependencies=[Depends(require_part_file_role(PartFileKind.DRAWING))],
)
async def upload_part_drawing(
    part_id: int,
    file: UploadFile = File(...),
    svc: PartFileService = Depends(get_part_file_service),
) -> PartFileOut:
    data = await file.read()
    return await svc.upload(
        owner_id=part_id,
        kind=PartFileKind.DRAWING,
        data=data,
        original_filename=file.filename or "drawing.pdf",
        content_type=file.content_type,
    )


@part_file_router.post(
    "/{part_id}/3d-models",
    response_model=PartFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为零件上传 3D 模型（STEP/STP/IGES/IGS/STL/OBJ/3MF，MANAGER + CLERK；自动覆盖旧文件）",
    dependencies=[Depends(require_part_file_role(PartFileKind.THREE_D_MODEL))],
)
async def upload_part_3d_model(
    part_id: int,
    file: UploadFile = File(...),
    svc: PartFileService = Depends(get_part_file_service),
) -> PartFileOut:
    data = await file.read()
    return await svc.upload(
        owner_id=part_id,
        kind=PartFileKind.THREE_D_MODEL,
        data=data,
        original_filename=file.filename or "model.stp",
        content_type=file.content_type,
    )


@part_file_router.post(
    "/{part_id}/cad-files",
    response_model=PartFileOut,
    status_code=http_status.HTTP_201_CREATED,
    summary=(
        "为零件上传 CAD 源文件（DWG/DXF，MANAGER + CLERK；自动覆盖旧文件）。"
        "2026-07-14 新增 kind=CAD_2D：与 PDF 图纸生命周期分离，"
        "删除 CAD 源不影响打印用 PDF。"
    ),
    dependencies=[Depends(require_part_file_role(PartFileKind.CAD_2D))],
)
async def upload_part_cad_file(
    part_id: int,
    file: UploadFile = File(...),
    svc: PartFileService = Depends(get_part_file_service),
) -> PartFileOut:
    data = await file.read()
    return await svc.upload(
        owner_id=part_id,
        kind=PartFileKind.CAD_2D,
        data=data,
        original_filename=file.filename or "source.dwg",
        content_type=file.content_type,
    )


@part_file_router.get(
    "/{part_id}/files",
    response_model=list[PartFileOut],
    summary="列出零件的文件（任意已登录；可选 kind 过滤）",
)
async def list_part_files(
    part_id: int,
    kind: str | None = None,
    svc: PartFileService = Depends(get_part_file_service),
) -> list[PartFileOut]:
    return await svc.list_for_part(part_id, kind=kind)


# ---------- 文件级操作（不关心归属） ----------
file_router = APIRouter(
    prefix="/files",
    tags=["零件文件"],
    dependencies=[Depends(get_current_user)],
)


@file_router.get(
    "/{file_id}/download-url",
    summary="重新签发单文件临时下载 URL（任意已登录用户）",
)
async def get_download_url(
    file_id: int,
    svc: PartFileService = Depends(get_part_file_service),
) -> dict:
    url = await svc.get_download_url(file_id)
    return {"url": url}


@file_router.get(
    "/{file_id}/content",
    summary="通过后端代理获取文件内容（预览/下载，任意已登录用户）",
)
async def get_file_content(
    file_id: int,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    svc: PartFileService = Depends(get_part_file_service),
):
    # 304 路径只查 DB 不下 COS（service 内仍走 _object_cache 内存命中，
    # 但 sha 校验失败时 0 字节返回前不进下载流程）
    meta = await svc.get_meta_for_304(file_id)
    if meta is None:
        from core.error_code import ErrCode
        from core.exception import BizError
        raise BizError(
            code=ErrCode.BIZ_PART_FILE_NOT_FOUND,
            message=f"file {file_id} not found",
            http_status=404,
        )
    if if_none_match and meta.content_sha256 and (
        if_none_match.strip().strip('"') == meta.content_sha256
    ):
        return Response(
            status_code=304,
            headers={"ETag": f'"{meta.content_sha256}"'},
        )
    data, content_type, filename = await svc.get_file_content(file_id)
    encoded = quote(filename, safe="")
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded}",
        "Cache-Control": "private, max-age=600",
    }
    if meta.content_sha256:
        headers["ETag"] = f'"{meta.content_sha256}"'
    return Response(
        content=data,
        media_type=content_type,
        headers=headers,
    )


@file_router.post(
    "/{file_id}/delete",
    summary="软删文件（COS 异步清理；按文件 kind 自动校验角色）",
)
async def delete_file(
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
    # 按 file.kind 派角色
    dep = require_part_file_role(PartFileKind(f.kind))
    # 重新跑一次依赖校验（不能直接 await dep，需要重新构造 CurrentUser）
    await dep(user)
    await svc.delete_file(file_id)
    return {"ok": True}