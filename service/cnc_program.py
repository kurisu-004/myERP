"""CNC 程序（G 代码）文件 service。

提供：
- upload_to_part：上传 G 代码到 COS + 写 t_cnc_program
- list_for_part：列出零件的 G 代码文件（带即时签名 URL）
- get_download_url / get_file_content：下载
- delete_file：软删 + 异步 COS 清理

COS 调用复用 `core.cos` 的 async 包装；文件校验复用 `service.drawing` 的
`_check_size` / `_check_allowed` / `_guess_content_type`（白名单已含 G 代码扩展名）。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import status as http_status

from core import cos as cos_mod
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TCncProgram
from repository.cnc_program import CncProgramRepository
from repository.part import PartRepository
from schema.cnc_program import CncProgramOut
from service.drawing import _check_allowed, _check_size, _guess_content_type
from utils.id_gen import new_id

_logger = logging.getLogger(__name__)


def _make_key_for_part(part_id: int, file_id: int, ext: str) -> str:
    return f"{settings.cos_upload_prefix}cnc/part/{part_id}/{file_id}.{ext}"


class CncProgramService:
    def __init__(
        self,
        programs: CncProgramRepository,
        parts: PartRepository | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.programs = programs
        self.parts = parts or PartRepository(programs.session)
        self._user_id: int | None = current_user.id if current_user else None

    async def upload_to_part(
        self,
        part_id: int,
        *,
        data: bytes,
        original_filename: str,
        content_type: str | None,
    ) -> CncProgramOut:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        _check_size(len(data))
        ext = _check_allowed(original_filename)
        ct = _guess_content_type(original_filename, content_type)

        file_id = new_id()
        key = _make_key_for_part(part_id, file_id, ext)

        try:
            await cos_mod.upload_object(key, data, ct)
        except BizError:
            asyncio.create_task(
                cos_mod.delete_object(key),
                name=f"cos-cleanup-{key}",
            )
            raise

        file_row = TCncProgram(
            id=file_id,
            part_id=part_id,
            file_type=ext.upper(),
            object_key=key,
            original_filename=original_filename,
            file_size=len(data),
            content_type=ct,
            upload_status="READY",
        )
        file_row.created_by = self._user_id
        file_row.updated_by = self._user_id
        await self.programs.create(file_row)
        return await self._to_out(file_row)

    async def list_for_part(self, part_id: int) -> list[CncProgramOut]:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        rows = await self.programs.list_by_part(part_id)
        return [await self._to_out(r) for r in rows]

    async def get_download_url(self, file_id: int) -> str:
        f = await self._get_or_404(file_id)
        return await cos_mod.presigned_get_url(f.object_key)

    async def get_file_content(self, file_id: int) -> tuple[bytes, str, str]:
        f = await self._get_or_404(file_id)
        data = await cos_mod.download_object(f.object_key)
        return data, f.content_type, f.original_filename

    async def delete_file(self, file_id: int) -> None:
        f = await self._get_or_404(file_id)
        key = f.object_key
        f.updated_by = self._user_id
        await self.programs.soft_delete(f)
        asyncio.create_task(self._safe_delete_cos(key))

    async def _get_or_404(self, file_id: int) -> TCncProgram:
        f = await self.programs.get_by_id(file_id)
        if f is None:
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_NOT_FOUND,
                message=f"cnc program {file_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return f

    async def _safe_delete_cos(self, key: str) -> None:
        try:
            await cos_mod.delete_object(key)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to delete COS object %s", key)

    async def _to_out(self, f: TCncProgram) -> CncProgramOut:
        download_url = await cos_mod.presigned_get_url(f.object_key)
        return CncProgramOut(
            id=f.id,
            part_id=f.part_id,
            file_type=f.file_type,
            original_filename=f.original_filename,
            file_size=f.file_size,
            content_type=f.content_type,
            download_url=download_url,
            upload_status=f.upload_status,
            created_at=f.created_at,
        )
