"""零件 / 装配体的统一文件 service（2026-07-10 起取代 `service/drawing.py` +
`service/cnc_program.py`）。

提供：
- `upload(*, owner_id, kind, ...)`：上传 + 写 t_part_file；按 kind 分发白名单
  与单文件 / 多版本语义。
- `list_for_part` / `list_for_owner` / `list_for_assembly`：列文件
  （带即时签名 URL）。
- `get_download_url` / `get_file_content`：单文件下载 / 预览。
- `delete_file`：软删 + 异步 COS 清理。

所有 COS 调用都用 `core.cos` 提供的 async 包装。

设计要点：
- 单文件 kind（DRAWING / 3D_MODEL / SETUP_SHEET / ASSEMBLY_MASTER）：上传前
  先 `soft_delete_by_part_and_kind` 旧的，收集旧 COS object_key 用于
  后续 fire-and-forget 清理；新行在 commit 后清理旧 COS。
- 多版本 kind（G_CODE）：直接 `create` 新行，不删旧的。
- 装配体的总装图（kind=ASSEMBLY_MASTER）由 `AssemblyService.create_assembly`
  在同一事务里写入，调用方负责校验 assembly 存在。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from fastapi import status as http_status

from core import cos as cos_mod
from core._file_kind_policy import (
    ALLOWED_EXTS_BY_KIND,
    SINGLE_FILE_KINDS,
)
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TPartFile
from model.enums import PartFileKind
from repository.part_file import PartFileRepository
from schema.part_file import PartFileOut
from utils.id_gen import new_id

_logger = logging.getLogger(__name__)


# ----- 工具函数（inline 在这里，原本在 service/drawing.py）-----
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


def _check_size(size: int) -> None:
    if size <= 0:
        raise BizError(
            code=ErrCode.BIZ_PART_FILE_TOO_LARGE,
            message="empty file",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    if size > settings.cos_max_file_size_bytes:
        raise BizError(
            code=ErrCode.BIZ_PART_FILE_TOO_LARGE,
            message=(
                f"file size {size} > max "
                f"{settings.cos_max_file_size_bytes}"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )


def _make_key_for_part(part_id: int, file_id: int, ext: str) -> str:
    """零件 / 装配体的子件文件 COS key。"""
    return f"{settings.cos_upload_prefix}part/{part_id}/{file_id}.{ext}"


def _make_key_for_assembly_master(assembly_id: int, file_id: int, ext: str) -> str:
    """装配体总装图 COS key（与 part key 共享 prefix，但目录名不同）。"""
    return f"{settings.cos_upload_prefix}assembly/{assembly_id}/{file_id}.{ext}"


async def _safe_delete_cos(key: str) -> None:
    """fire-and-forget COS 清理（异常吞掉，仅 log）。"""
    try:
        await cos_mod.delete_object(key)
    except Exception:  # noqa: BLE001
        _logger.exception("failed to delete COS object %s", key)


def _check_allowed_for_kind(filename: str, kind: PartFileKind) -> str:
    """校验扩展名与 kind 白名单匹配，返回小写扩展名。"""
    ext = _normalize_ext(filename)
    allowed = ALLOWED_EXTS_BY_KIND[kind]
    if ext not in allowed:
        raise BizError(
            code=ErrCode.BIZ_PART_FILE_BAD_TYPE,
            message=(
                f"kind={kind.value} 仅支持扩展名 {sorted(allowed)}，"
                f"当前为 '.{ext or '(无)'}'"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    return ext


class PartFileService:
    def __init__(
        self,
        files: PartFileRepository,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.files = files
        self._user_id: int | None = current_user.id if current_user else None

    # ===== 上传 =====
    async def upload(
        self,
        *,
        owner_id: int,
        kind: PartFileKind | str,
        data: bytes,
        original_filename: str,
        content_type: str | None,
    ) -> PartFileOut:
        """上传文件到 COS + 写 t_part_file。

        `owner_id` 是 polymorphic owner：
        - 大多数 kind：真实 t_part.id
        - kind == ASSEMBLY_MASTER：t_assembly.id（装配体总图）

        单文件 kind（DRAWING / 3D_MODEL / SETUP_SHEET / ASSEMBLY_MASTER）会在
        上传前先 soft_delete 同一 (owner_id, kind) 的旧行；G_CODE 直接 create。
        """
        if isinstance(kind, str):
            kind = PartFileKind(kind)
        _check_size(len(data))
        ext = _check_allowed_for_kind(original_filename, kind)
        ct = _guess_content_type(original_filename, content_type)

        # 单文件 kind：上传前先软删同 owner+kind 的旧行。
        old_keys: list[str] = []
        if kind in SINGLE_FILE_KINDS:
            old_keys = await self.files.soft_delete_by_part_and_kind(
                owner_id, kind.value
            )

        file_id = new_id()
        if kind == PartFileKind.ASSEMBLY_MASTER:
            key = _make_key_for_assembly_master(owner_id, file_id, ext)
        else:
            key = _make_key_for_part(owner_id, file_id, ext)

        try:
            await cos_mod.upload_object(key, data, ct)
        except BizError:
            asyncio.create_task(
                cos_mod.delete_object(key),
                name=f"cos-cleanup-{key}",
            )
            raise

        file_row = TPartFile(
            id=file_id,
            part_id=owner_id,
            kind=kind.value,
            file_type=ext.upper(),
            object_key=key,
            original_filename=original_filename,
            file_size=len(data),
            content_type=ct,
            upload_status="READY",
        )
        file_row.created_by = self._user_id
        file_row.updated_by = self._user_id
        await self.files.create(file_row)

        # 旧 COS 对象异步清理（不在事务关键路径上）
        for old_key in old_keys:
            asyncio.create_task(
                _safe_delete_cos(old_key),
                name=f"cos-cleanup-{old_key}",
            )

        return await self._to_out(file_row)

    # ===== 列表 =====
    async def list_for_part(
        self,
        owner_id: int,
        *,
        kind: PartFileKind | str | None = None,
    ) -> list[PartFileOut]:
        kind_str = kind.value if isinstance(kind, PartFileKind) else kind
        rows = await self.files.list_by_part(owner_id, kind=kind_str)
        return [await self._to_out(r) for r in rows]

    async def list_for_assembly(
        self,
        assembly_id: int,
        *,
        child_part_ids: list[int],
    ) -> list[PartFileOut]:
        """装配体的「总图 + 子件 drawings」聚合列表。"""
        rows = await self.files.list_for_assembly(
            assembly_id, child_part_ids=child_part_ids,
        )
        return [await self._to_out(r) for r in rows]

    # ===== 单文件操作 =====
    async def _get_or_404(
        self, file_id: int
    ) -> TPartFile:
        f = await self.files.get_by_id(file_id)
        if f is None:
            raise BizError(
                code=ErrCode.BIZ_PART_FILE_NOT_FOUND,
                message=f"file {file_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return f

    async def get_download_url(self, file_id: int) -> str:
        f = await self._get_or_404(file_id)
        return await cos_mod.presigned_get_url(f.object_key)

    async def get_file_content(
        self, file_id: int
    ) -> tuple[bytes, str, str]:
        """获取文件内容（用于后端代理预览/下载）。返回 (data, content_type, filename)。"""
        f = await self._get_or_404(file_id)
        data = await cos_mod.download_object(f.object_key)
        return data, f.content_type, f.original_filename

    async def delete_file(self, file_id: int) -> None:
        f = await self._get_or_404(file_id)
        key = f.object_key
        f.updated_by = self._user_id
        await self.files.soft_delete(f)
        asyncio.create_task(_safe_delete_cos(key))

    async def delete_files_silently(self, keys: Iterable[str]) -> None:
        """批量异步删除（service 内部使用，不抛错）。"""
        for k in keys:
            asyncio.create_task(_safe_delete_cos(k))

    # ===== 内部：DB 行 → 输出 schema + 签 URL =====
    async def _to_out(self, f: TPartFile) -> PartFileOut:
        download_url = await cos_mod.presigned_get_url(f.object_key)
        return PartFileOut(
            id=f.id,
            owner_id=f.part_id,                # polymorphic
            kind=f.kind,
            file_type=f.file_type,
            original_filename=f.original_filename,
            file_size=f.file_size,
            content_type=f.content_type,
            upload_status=f.upload_status,
            created_at=f.created_at,
            download_url=download_url,
        )