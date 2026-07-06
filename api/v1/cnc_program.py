"""CNC 程序（G 代码）文件 API。

子件程序：`/parts/{part_id}/cnc-programs`
程序级操作：`/cnc-programs/{file_id}/...`

权限模型（2026-07-06 调整）：
- 上传 / 删除：MANAGER + CNC_PROGRAMMER（编程员写程序）
- 列 / 预览 / 下载：任意已登录用户
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile, status as http_status
from fastapi.responses import Response

from api.deps import get_cnc_program_service
from core.permission import get_current_user, require_roles
from model.enums import UserRole
from schema.cnc_program import CncProgramOut
from service.cnc_program import CncProgramService


# 写侧守卫：上传 / 删除 G 代码 → MANAGER + CNC_PROGRAMMER。
_cnc_write_dep = [
    Depends(require_roles(UserRole.MANAGER, UserRole.CNC_PROGRAMMER))
]


# ---------- 子件程序（按 part） ----------
# router 级仅要求已登录；列接口无需 role guard，写接口在 route 级显式收紧。
child_program_router = APIRouter(
    prefix="/parts",
    tags=["CNC编程"],
    dependencies=[Depends(get_current_user)],
)


@child_program_router.post(
    "/{part_id}/cnc-programs",
    response_model=CncProgramOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为子零件上传 G 代码程序（MANAGER / CNC_PROGRAMMER）",
    dependencies=_cnc_write_dep,
)
async def upload_part_cnc_program(
    part_id: int,
    file: UploadFile = File(...),
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> CncProgramOut:
    data = await file.read()
    return await cnc.upload_to_part(
        part_id,
        data=data,
        original_filename=file.filename or "program.nc",
        content_type=file.content_type,
    )


@child_program_router.get(
    "/{part_id}/cnc-programs",
    response_model=list[CncProgramOut],
    summary="列出子零件的 G 代码程序（任意已登录用户）",
)
async def list_part_cnc_programs(
    part_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> list[CncProgramOut]:
    return await cnc.list_for_part(part_id)


# ---------- 程序级操作（不关心归属） ----------
# 列 / 预览 / 下载：任意已登录；删除：MANAGER + CNC_PROGRAMMER。
program_router = APIRouter(
    prefix="/cnc-programs",
    tags=["CNC编程"],
    dependencies=[Depends(get_current_user)],
)


@program_router.get(
    "/{file_id}/download-url",
    summary="重新签发单文件临时下载 URL（任意已登录用户）",
)
async def get_cnc_download_url(
    file_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> dict:
    url = await cnc.get_download_url(file_id)
    return {"url": url}


@program_router.get(
    "/{file_id}/content",
    summary="通过后端代理获取 G 代码内容（预览/下载，任意已登录用户）",
)
async def get_cnc_file_content(
    file_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
):
    data, content_type, filename = await cnc.get_file_content(file_id)
    encoded = quote(filename, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded}"},
    )


@program_router.post(
    "/{file_id}/delete",
    summary="软删 G 代码程序（COS 对象异步清理，MANAGER / CNC_PROGRAMMER）",
    dependencies=_cnc_write_dep,
)
async def delete_cnc_program(
    file_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> dict:
    await cnc.delete_file(file_id)
    return {"ok": True}
