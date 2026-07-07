"""图纸文件 service。

提供：
- upload_to_part / upload_to_assembly：上传文件到 COS + 写 t_drawing_file
- list_for_part / list_for_assembly：列文件（带即时签名 URL）
- delete_file：软删 + 异步 COS 清理
- get_download_url：单文件重新签发 URL

所有 COS 调用都用 `core.cos` 提供的 async 包装（同步 SDK 包到线程池）。
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
from model import TDrawingFile
from repository.assembly import AssemblyRepository
from repository.drawing_file import DrawingFileRepository
from repository.part import PartRepository
from schema.drawing import DrawingFileOut
from utils.id_gen import new_id

_logger = logging.getLogger(__name__)


# 内存中的扩展名 → COS content_type 映射（前端一般会带正确 mime，
# 这里只作 fallback）。
_EXT_TO_CONTENT_TYPE = {
    "pdf": "application/pdf",
    "step": "application/step",
    "stp": "application/step",
    "dwg": "application/acad",
    "dxf": "application/dxf",
}


def _normalize_ext(filename: str) -> str:
    """从文件名提取小写扩展名（无点号）；无扩展名返回空串。"""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _guess_content_type(filename: str, fallback: str | None) -> str:
    if fallback and fallback.strip():
        return fallback
    ext = _normalize_ext(filename)
    return _EXT_TO_CONTENT_TYPE.get(ext, "application/octet-stream")


def _check_allowed(filename: str) -> str:
    """校验扩展名是否在白名单，返回小写扩展名。"""
    ext = _normalize_ext(filename)
    allowed = {
        e.strip().lower()
        for e in settings.cos_allowed_types.split(",")
        if e.strip()
    }
    if ext not in allowed:
        raise BizError(
            code=ErrCode.BIZ_DRAWING_FILE_BAD_TYPE,
            message=(
                f"file extension '.{ext}' not allowed; "
                f"whitelist: {sorted(allowed)}"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    return ext


def _check_size(size: int) -> None:
    if size <= 0:
        raise BizError(
            code=ErrCode.BIZ_DRAWING_FILE_TOO_LARGE,
            message="empty file",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    if size > settings.cos_max_file_size_bytes:
        raise BizError(
            code=ErrCode.BIZ_DRAWING_FILE_TOO_LARGE,
            message=(
                f"file size {size} > max "
                f"{settings.cos_max_file_size_bytes}"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )


def _make_key_for_assembly(assembly_id: int, file_id: int, ext: str) -> str:
    return f"{settings.cos_upload_prefix}assembly/{assembly_id}/{file_id}.{ext}"


def _make_key_for_part(part_id: int, file_id: int, ext: str) -> str:
    return f"{settings.cos_upload_prefix}part/{part_id}/{file_id}.{ext}"


class DrawingService:
    def __init__(
        self,
        files: DrawingFileRepository,
        parts: PartRepository | None = None,
        assemblies: AssemblyRepository | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.files = files
        self.parts = parts or PartRepository(files.session)
        self.assemblies = assemblies or AssemblyRepository(files.session)
        self._user_id: int | None = current_user.id if current_user else None

    # ===== 上传 =====
    async def upload_to_assembly(
        self,
        assembly_id: int,
        *,
        data: bytes,
        original_filename: str,
        content_type: str | None,
        page_index: int | None = None,
    ) -> DrawingFileOut:
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        _check_size(len(data))
        ext = _check_allowed(original_filename)
        ct = _guess_content_type(original_filename, content_type)

        file_id = new_id()
        key = _make_key_for_assembly(assembly_id, file_id, ext)

        # 写 DB（PENDING）→ 上传 COS → 更新为 READY。
        # 这里直接同步上传 + 写 READY；调用方所在 service 已在外层事务里，
        # 若 COS 抛 BizError，整个事务回滚；已经上传的 COS 对象由调用方
        # 负责清理（在我们这边用 asyncio.gather 兜底）。
        try:
            await cos_mod.upload_object(key, data, ct)
        except BizError:
            # 触发后台异步清理（虽然 DB 还没写所以这里是 noop，但仍调用一次）
            asyncio.create_task(
                cos_mod.delete_object(key),
                name=f"cos-cleanup-{key}",
            )
            raise

        file_row = TDrawingFile(
            id=file_id,
            assembly_id=assembly_id,
            part_id=None,
            file_type=ext.upper(),
            object_key=key,
            original_filename=original_filename,
            file_size=len(data),
            content_type=ct,
            page_index=page_index,
            upload_status="READY",
        )
        file_row.created_by = self._user_id
        file_row.updated_by = self._user_id
        await self.files.create(file_row)
        return await self._to_out(file_row)

    async def upload_to_part(
        self,
        part_id: int,
        *,
        data: bytes,
        original_filename: str,
        content_type: str | None,
        page_index: int | None = None,
    ) -> DrawingFileOut:
        """零件上传图纸。

        限制（2026-07-07 起）：
        - 文件格式仅接受 PDF（不再支持 STEP/DWG/DXF/PNG/JPG 等）。
        - 每个零件最多存在 1 个图纸文件：新上传会软删旧的 t_drawing_file
          行，并 fire-and-forget 异步清理旧 COS 对象；旧 row 在事务内完成
          软删，新 row 在同一事务里 create，保证「先删后建」一致。
        - 装配体的图纸不受此限制（沿用多文件语义；装配体本身是图文档归档）。
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        _check_size(len(data))
        ext = _normalize_ext(original_filename)
        if ext != "pdf":
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_BAD_TYPE,
                message=(
                    f"零件图纸仅支持 PDF，当前为 '.{ext or '(无)'}'"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        ct = _guess_content_type(original_filename, content_type)

        # 单文件覆盖语义：列出当前 part 的所有未软删图纸，逐个软删并
        # 收集 COS key 用于后续 fire-and-forget 清理。注意：这里 DB 操作
        # 在调用方 session/事务里同步执行；COS 删除走异步，保证事务
        # 回滚时不会留下新对象。
        existing = await self.files.list_by_part(part_id)
        old_keys: list[str] = []
        for old in existing:
            if old.deleted_at is None:
                old.updated_by = self._user_id
                await self.files.soft_delete(old)
                old_keys.append(old.object_key)

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

        file_row = TDrawingFile(
            id=file_id,
            part_id=part_id,
            assembly_id=None,
            file_type=ext.upper(),
            object_key=key,
            original_filename=original_filename,
            file_size=len(data),
            content_type=ct,
            page_index=page_index,
            upload_status="READY",
        )
        file_row.created_by = self._user_id
        file_row.updated_by = self._user_id
        await self.files.create(file_row)

        # 上传成功后才发起旧 COS 清理（避免新建失败时旧文件也被清掉）
        for old_key in old_keys:
            asyncio.create_task(
                self._safe_delete_cos(old_key),
                name=f"cos-cleanup-{old_key}",
            )

        return await self._to_out(file_row)

    # ===== 列表 + 即时签名 =====
    async def list_for_part(
        self, part_id: int
    ) -> list[DrawingFileOut]:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        rows = await self.files.list_by_part(part_id)
        return [await self._to_out(r) for r in rows]

    async def list_for_assembly(
        self, assembly_id: int
    ) -> list[DrawingFileOut]:
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        rows = await self.files.list_by_assembly(assembly_id)
        return [await self._to_out(r) for r in rows]

    async def get_download_url(self, file_id: int) -> str:
        f = await self.files.get_by_id(file_id)
        if f is None:
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_NOT_FOUND,
                message=f"file {file_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return await cos_mod.presigned_get_url(f.object_key)

    async def get_file_content(self, file_id: int) -> tuple[bytes, str, str]:
        """获取文件内容（用于后端代理预览/下载）。

        返回 (data, content_type, filename) 三元组。
        """
        f = await self.files.get_by_id(file_id)
        if f is None:
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_NOT_FOUND,
                message=f"file {file_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        data = await cos_mod.download_object(f.object_key)
        return data, f.content_type, f.original_filename

    # ===== 软删 + 异步 COS 清理 =====
    async def delete_file(self, file_id: int) -> None:
        f = await self.files.get_by_id(file_id)
        if f is None:
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_NOT_FOUND,
                message=f"file {file_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        key = f.object_key
        f.updated_by = self._user_id
        await self.files.soft_delete(f)
        # fire-and-forget：COS 删除失败不影响主业务，由后台 GC 重试
        asyncio.create_task(self._safe_delete_cos(key))

    async def delete_files_silently(self, keys: list[str]) -> None:
        """批量异步删除（service 内部使用，不抛错）。"""
        if not keys:
            return
        for k in keys:
            asyncio.create_task(self._safe_delete_cos(k))

    async def _safe_delete_cos(self, key: str) -> None:
        try:
            await cos_mod.delete_object(key)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to delete COS object %s", key)

    # ===== 内部：DB 行 → 输出 schema + 签 URL =====
    async def _to_out(self, f: TDrawingFile) -> DrawingFileOut:
        owner_type = "assembly" if f.assembly_id is not None else "part"
        owner_id = f.assembly_id if f.assembly_id is not None else (f.part_id or 0)
        download_url = await cos_mod.presigned_get_url(f.object_key)
        return DrawingFileOut(
            id=f.id,
            owner_type=owner_type,
            owner_id=owner_id,
            file_type=f.file_type,
            original_filename=f.original_filename,
            file_size=f.file_size,
            content_type=f.content_type,
            page_index=f.page_index,
            download_url=download_url,
            upload_status=f.upload_status,
            created_at=f.created_at,
        )
