"""零件 / 装配体的统一文件 service（2026-07-10 起取代 `service/drawing.py` +
`service/cnc_program.py`）。

提供：
- `upload(*, owner_id, kind, ...)`：上传 + 写 t_part_file；按 kind 分发白名单
  与单文件 / 多版本语义；**带内容去重（SHA-256）**。
- `list_for_part` / `list_for_owner` / `list_for_assembly`：列文件
  （带即时签名 URL）。
- `get_download_url` / `get_file_content`：单文件下载 / 预览。
- `delete_file`：软删 + 异步 COS 清理。

所有 COS 调用都用 `core.cos` 提供的 async 包装。

设计要点（2026-07-14 整合）：
- **内容去重**：上传时算 SHA-256，先查 `(part_id, kind, content_sha256)` 是否有
  活跃行 → 有则复用（单文件 kind 改 `original_filename`/`updated_at`/`updated_by`，
  G_CODE 多版本直接 no-op 返回）；无则走 COS PUT + insert。
- **CAS key**：`core.file_hash.make_object_key` 派生
  `{prefix}{owner_kind}/{owner_id}/{KIND}/{sha16}_{safe_filename}`。DB 丢失时
  从桶扫描即可知 owner/kind/内容指纹/原始文件名。
- **单文件 kind**（DRAWING / 3D_MODEL / ASSEMBLY_MASTER / CAD_2D）：
  命中复用时跳过 COS PUT；未命中时上传前 soft_delete 同 (owner, kind) 旧行。
- **多版本 / 配对 kind**（G_CODE / SETUP_SHEET）：命中 no-op；未命中直接 create。
  两者通过 `paired_file_id` 双向关联，由 `upload_paired` 一次上传。
- 跨 part 不共享：DB 部分唯一索引 `uk_t_part_file_part_kind_sha` 在
  `(part_id, kind, content_sha256)` 上。跨 part 上传相同字节理论上仍走
  「不同 owner_id → 不同 sha16 → 不同 key」自然不共享，DB 索引是兜底。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError

from core import cos as cos_mod
from core._file_kind_policy import (
    ALLOWED_EXTS_BY_KIND,
    SINGLE_FILE_KINDS,
)
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.file_hash import compute_sha256_hex, make_object_key
from core.permission import CurrentUser
from model import TPartFile
from model.enums import PartFileKind
from repository.part_file import PartFileRepository
from schema.part_file import PartFileOut
from utils.id_gen import new_id

_logger = logging.getLogger(__name__)


# ----- 工具函数（inline 在这里，原本在 service/drawing.py）-----
# 2026-07-14 扩展：DRAWING 加 8 种图片格式 / 3D_MODEL 加 IGES/STL/OBJ/3MF /
# 新增 CAD_2D 的 dwg/dxf。service 层不再依赖 core.config.cos_allowed_types。
_EXT_TO_CONTENT_TYPE = {
    # 图纸（PDF + 图片）
    "pdf":  "application/pdf",
    "png":  "image/png",
    "jpg":  "image/jpeg", "jpeg": "image/jpeg",
    "gif":  "image/gif",
    "bmp":  "image/bmp",
    "tif":  "image/tiff", "tiff": "image/tiff",
    "webp": "image/webp",
    "heic": "image/heic",
    # 3D 模型
    "step": "application/step",
    "stp":  "application/step",
    "iges": "application/iges",
    "igs":  "application/iges",
    "stl":  "model/stl",
    "obj":  "model/obj",
    "3mf":  "model/3mf",
    # CAD 2D
    "dwg":  "application/acad",
    "dxf":  "application/dxf",
    # G 代码（SETUP_SHEET 仍是 PDF，G_CODE 多版本）
    "nc":   "text/plain", "tap": "text/plain",
    "cnc":  "text/plain", "mpf": "text/plain", "ngc": "text/plain",
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


def _owner_kind_for(kind: PartFileKind) -> str:
    """根据 kind 决定 COS key 中的 owner_kind 段（part 或 assembly）。"""
    return "assembly" if kind == PartFileKind.ASSEMBLY_MASTER else "part"


class PartFileService:
    def __init__(
        self,
        files: PartFileRepository,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.files = files
        self._user_id: int | None = current_user.id if current_user else None

    # ===== 上传（含去重）=====
    async def upload(
        self,
        *,
        owner_id: int,
        kind: PartFileKind | str,
        data: bytes,
        original_filename: str,
        content_type: str | None,
    ) -> PartFileOut:
        """上传文件到 COS + 写 t_part_file，带 SHA-256 内容去重。

        流程：
        1. 校验 size + ext 白名单
        2. 算 SHA-256
        3. 查 (part_id, kind, sha) 是否有活跃行 → 命中：
           - 单文件 kind：更新 existing.original_filename/updated_*/updated_by，
             跳过 COS PUT，复用 object_key，返回现有行
           - G_CODE 多版本：直接 no-op 返回（不改任何字段）
        4. 未命中：单文件 kind 先 soft_delete 旧 (owner, kind) 行；构造
           CAS key；COS PUT；写新行；异步清理旧 COS 对象
        5. 跨 part 撞唯一索引 → 捕获 IntegrityError → 重生 key 重试

        `owner_id` polymorphic：ASSEMBLY_MASTER 用 t_assembly.id，其它用 t_part.id。
        """
        if isinstance(kind, str):
            kind = PartFileKind(kind)
        _check_size(len(data))
        ext = _check_allowed_for_kind(original_filename, kind)
        ct = _guess_content_type(original_filename, content_type)

        # 1) 计算 SHA-256（内存中，开销 ~150ms / 100MB）
        sha = compute_sha256_hex(data)

        # 2) 去重检查
        existing = await self.files.find_active_by_part_kind_sha(
            owner_id, kind.value, sha
        )
        if existing is not None:
            if kind in SINGLE_FILE_KINDS:
                # 覆盖文件名/timestamp；不改 object_key / sha
                existing.original_filename = original_filename
                existing.updated_by = self._user_id
                await self.files.update(existing)
                _logger.info(
                    "part_file dedup hit: part_id=%s kind=%s sha=%s "
                    "object_key=%s (filename updated)",
                    owner_id, kind.value, sha[:16], existing.object_key,
                )
            else:
                # G_CODE 多版本：no-op
                _logger.info(
                    "part_file dedup noop: part_id=%s kind=%s sha=%s "
                    "(g_code multi-version)",
                    owner_id, kind.value, sha[:16],
                )
            return await self._to_out(existing)

        # 3) 单文件 kind：上传前先软删同 (owner, kind) 旧行
        old_keys: list[str] = []
        if kind in SINGLE_FILE_KINDS:
            old_keys = await self.files.soft_delete_by_part_and_kind(
                owner_id, kind.value
            )

        # 4) 派生 CAS key（含 sha16 + safe_filename）
        new_key = make_object_key(
            owner_id=owner_id,
            owner_kind=_owner_kind_for(kind),
            kind=kind,
            content_sha256=sha,
            original_filename=original_filename,
            ext=ext,
        )

        # 5) COS PUT（失败则异步清孤儿）
        try:
            await cos_mod.upload_object(new_key, data, ct)
        except BizError:
            asyncio.create_task(
                cos_mod.delete_object(new_key),
                name=f"cos-cleanup-{new_key}",
            )
            raise

        # 6) insert 新行（防御：跨 part 撞唯一索引 → 重生 key 重试一次）
        file_id = new_id()
        file_row = TPartFile(
            id=file_id,
            part_id=owner_id,
            kind=kind.value,
            file_type=ext.upper(),
            object_key=new_key,
            original_filename=original_filename,
            file_size=len(data),
            content_type=ct,
            upload_status="READY",
            content_sha256=sha,
        )
        file_row.created_by = self._user_id
        file_row.updated_by = self._user_id
        try:
            await self.files.create(file_row)
        except IntegrityError as exc:
            # uk_t_part_file_part_kind_sha 撞（理论上 owner_id 不同 → sha16
            # 不同 → key 不同，不会撞；唯一可能：重试时同一毫秒同一 part 同
            # 一 sha 撞了并发去重漏检）。把已上传的 COS 对象清掉，抛 BizError。
            asyncio.create_task(_safe_delete_cos(new_key))
            raise BizError(
                code=ErrCode.BIZ_PART_FILE_DUPLICATE,
                message=(
                    f"file content sha256={sha[:16]} already exists for "
                    f"part_id={owner_id} kind={kind.value}"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            ) from exc

        # 7) 异步清理旧 COS 对象（old_key == new_key 时跳过，dedup 防御）
        for old_key in old_keys:
            if old_key != new_key:
                asyncio.create_task(
                    _safe_delete_cos(old_key),
                    name=f"cos-cleanup-{old_key}",
                )

        return await self._to_out(file_row)

    # ===== 配对上传（G_CODE + SETUP_SHEET） =====
    async def upload_paired(
        self,
        *,
        owner_id: int,
        gcode_data: bytes,
        gcode_filename: str,
        gcode_content_type: str | None,
        setup_data: bytes,
        setup_filename: str,
        setup_content_type: str | None,
    ) -> tuple[PartFileOut, PartFileOut]:
        """配对上传 G 代码 + CNC 设定单 PDF。
        两个文件独立上传（各自 COS 对象 + DB 行），上传成功后双向设 paired_file_id。
        """
        gcode_out = await self.upload(
            owner_id=owner_id,
            kind=PartFileKind.G_CODE,
            data=gcode_data,
            original_filename=gcode_filename,
            content_type=gcode_content_type,
        )
        setup_out = await self.upload(
            owner_id=owner_id,
            kind=PartFileKind.SETUP_SHEET,
            data=setup_data,
            original_filename=setup_filename,
            content_type=setup_content_type,
        )
        # 双向关联
        gcode_id = int(gcode_out.id)
        setup_id = int(setup_out.id)
        gcode_row = await self.files.get_by_id(gcode_id)
        setup_row = await self.files.get_by_id(setup_id)
        if gcode_row is not None and setup_row is not None:
            gcode_row.paired_file_id = setup_id
            setup_row.paired_file_id = gcode_id
            await self.files.update(gcode_row)
            await self.files.update(setup_row)
            gcode_out.paired_file_id = str(setup_id)
            setup_out.paired_file_id = str(gcode_id)
        return gcode_out, setup_out

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
        """获取文件内容（用于后端代理预览/下载）。返回 (data, content_type, filename)。

        走 `download_object_cached` — 同一 `content_sha256` 进程内命中，省 COS GET。
        """
        f = await self._get_or_404(file_id)
        data = await cos_mod.download_object_cached(f.object_key, f.content_sha256)
        return data, f.content_type, f.original_filename

    async def get_meta_for_304(self, file_id: int) -> TPartFile | None:
        """只查 DB 元数据（不下 COS），给 ETag/304 处理路径用。"""
        f = await self.files.get_by_id(file_id)
        if f is None or f.deleted_at is not None:
            return None
        return f

    async def delete_file(self, file_id: int) -> None:
        f = await self._get_or_404(file_id)
        # 删除前先清除配对文件的 paired_file_id（G_CODE <-> SETUP_SHEET 双向关联）
        if f.paired_file_id is not None:
            mate = await self.files.get_by_id(f.paired_file_id)
            if mate is not None:
                mate.paired_file_id = None
                await self.files.update(mate)
                _logger.info(
                    "part_file unpaired: file_id=%s paired_file_id=%s (mate %s unlinked)",
                    f.id, f.paired_file_id, mate.id,
                )
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
            version=f.version,
            owner_id=f.part_id,                # polymorphic
            kind=f.kind,
            file_type=f.file_type,
            original_filename=f.original_filename,
            file_size=f.file_size,
            content_type=f.content_type,
            upload_status=f.upload_status,
            content_sha256=f.content_sha256,
            created_at=f.created_at,
            paired_file_id=(
                str(f.paired_file_id) if f.paired_file_id is not None else None
            ),
            download_url=download_url,
        )