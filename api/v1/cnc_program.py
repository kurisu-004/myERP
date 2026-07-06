"""CNC 程序（G 代码）文件 API。

子件程序：`/parts/{part_id}/cnc-programs`
程序级操作：`/cnc-programs/{file_id}/...`

权限：上传 MANAGER+CNC_PROGRAMMER；列表/下载 MANAGER+CLERK+CNC_PROGRAMMER。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile, status as http_status
from fastapi.responses import Response

from api.deps import get_cnc_program_service
from core.permission import require_roles
from model.enums import UserRole
from schema.cnc_program import CncProgramOut
from service.cnc_program import CncProgramService


# ---------- 子件程序（按 part） ----------
child_program_router = APIRouter(
    prefix="/parts",
    tags=["CNC编程"],
    dependencies=[Depends(require_roles(UserRole.MANAGER, UserRole.CNC_PROGRAMMER))],
)


@child_program_router.post(
    "/{part_id}/cnc-programs",
    response_model=CncProgramOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="为子零件上传 G 代码程序（MANAGER / CNC_PROGRAMMER）",
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
    summary="列出子零件的 G 代码程序（MANAGER / CLERK / CNC_PROGRAMMER）",
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        ))
    ],
)
async def list_part_cnc_programs(
    part_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> list[CncProgramOut]:
    return await cnc.list_for_part(part_id)


# ---------- 程序级操作（不关心归属） ----------
program_router = APIRouter(
    prefix="/cnc-programs",
    tags=["CNC编程"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        ))
    ],
)


@program_router.get(
    "/{file_id}/download-url",
    summary="重新签发单文件临时下载 URL",
)
async def get_cnc_download_url(
    file_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> dict:
    url = await cnc.get_download_url(file_id)
    return {"url": url}


@program_router.get(
    "/{file_id}/content",
    summary="通过后端代理获取 G 代码内容（预览/下载）",
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
    summary="软删 G 代码程序（COS 对象异步清理）",
    dependencies=[Depends(require_roles(UserRole.MANAGER, UserRole.CNC_PROGRAMMER))],
)
async def delete_cnc_program(
    file_id: int,
    cnc: CncProgramService = Depends(get_cnc_program_service),
) -> dict:
    await cnc.delete_file(file_id)
    return {"ok": True}
