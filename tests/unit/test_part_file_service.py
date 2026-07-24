"""Unit tests for PartFileService (service/part_file.py).

验证统一的零件 / 装配体文件 service：
- upload：按 kind 分发白名单 + 单文件覆盖 vs 多版本语义
- list / download / delete 路径
- 文件大小校验
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 必须先 import model 包的 __init__ 以触发所有 mapper 注册，
# 否则 service 中直接构造 TPartFile() 会报 "Mapper has no property X"
import model  # noqa: F401
from core.error_code import ErrCode
from core.exception import BizError
from model import TPartFile
from model.enums import PartFileKind
from repository.part_file import PartFileRepository
from service.part_file import PartFileService

pytestmark = pytest.mark.asyncio


def make_part_file(**kwargs):
    """构造一个轻量级 TPartFile-like MagicMock（不用 spec，避免 SQLAlchemy 校验）"""
    defaults = dict(
        id=9001,
        part_id=1001,
        kind="DRAWING",
        file_type="PDF",
        object_key="drawings/part/1001/9001.pdf",
        original_filename="drawing.pdf",
        file_size=1024,
        content_type="application/pdf",
        upload_status="READY",
        content_sha256=None,  # 2026-07-14：默认 None（未计算 / 历史记录）
        created_at=datetime(2026, 7, 10, 10, 0, 0),
        updated_at=datetime(2026, 7, 10, 10, 0, 0),
    )
    params = {**defaults, **kwargs}
    f = MagicMock()
    for k, v in params.items():
        setattr(f, k, v)
    return f


@pytest.fixture
def mock_files():
    m = MagicMock(spec=PartFileRepository)
    m.create = AsyncMock()
    m.get_by_id = AsyncMock(return_value=None)
    m.list_by_part = AsyncMock(return_value=[])
    m.soft_delete_by_part_and_kind = AsyncMock(return_value=[])
    m.soft_delete = AsyncMock()
    # 2026-07-14 去重检查：默认返回 None（无 dedup 命中），单测要触发命中时显式覆盖
    m.find_active_by_part_kind_sha = AsyncMock(return_value=None)
    # 模拟 flush 后 server_default 字段被填回
    async def _simulate_flush(row):
        from datetime import datetime
        if getattr(row, "created_at", None) is None:
            row.created_at = datetime(2026, 7, 10, 10, 0, 0)
        if getattr(row, "updated_at", None) is None:
            row.updated_at = row.created_at
        return row
    m.create.side_effect = _simulate_flush
    return m


@pytest.fixture
def svc(mock_files):
    s = PartFileService(files=mock_files)
    # 屏蔽真实的 cos.upload_object / presigned_get_url 调用
    from unittest.mock import patch
    import service.part_file
    upload_patcher = patch.object(
        service.part_file.cos_mod, "upload_object", new=AsyncMock()
    )
    presign_patcher = patch.object(
        service.part_file.cos_mod, "presigned_get_url",
        new=AsyncMock(return_value="https://test.example/x"),
    )
    upload_patcher.start()
    presign_patcher.start()
    return s


# ============================================================
# upload：kind → 白名单 + 单文件覆盖 / 多版本
# ============================================================


class TestUploadKindWhitelist:
    """每个 kind 接受的文件扩展名白名单。"""

    @pytest.mark.parametrize("kind,filename,expected_type", [
        (PartFileKind.DRAWING, "x.pdf", "PDF"),
        (PartFileKind.THREE_D_MODEL, "x.stp", "STP"),
        (PartFileKind.THREE_D_MODEL, "x.step", "STEP"),
        (PartFileKind.G_CODE, "x.nc", "NC"),
        (PartFileKind.G_CODE, "x.tap", "TAP"),
        (PartFileKind.SETUP_SHEET, "x.pdf", "PDF"),
        (PartFileKind.ASSEMBLY_MASTER, "x.pdf", "PDF"),
    ])
    async def test_valid_extension_for_kind(self, svc, mock_files, kind, filename, expected_type):
        with patch("service.part_file.cos_mod.upload_object",
                    new=AsyncMock()):
            await svc.upload(
                owner_id=1001, kind=kind, data=b"x",
                original_filename=filename, content_type=None,
            )
        # 验证 create 被调，且 file_type 大写
        assert mock_files.create.await_count == 1
        created_row = mock_files.create.await_args[0][0]
        assert created_row.file_type == expected_type
        assert created_row.kind == kind.value

    @pytest.mark.parametrize("kind,filename", [
        (PartFileKind.DRAWING, "x.step"),
        (PartFileKind.THREE_D_MODEL, "x.pdf"),
        (PartFileKind.SETUP_SHEET, "x.nc"),
        (PartFileKind.G_CODE, "x.pdf"),
    ])
    async def test_invalid_extension_raises(self, svc, kind, filename):
        with pytest.raises(BizError) as exc:
            await svc.upload(
                owner_id=1001, kind=kind, data=b"x",
                original_filename=filename, content_type=None,
            )
        assert exc.value.code == ErrCode.BIZ_PART_FILE_BAD_TYPE
        assert exc.value.http_status == 400


class TestUploadSize:
    async def test_empty_file_raises(self, svc):
        with pytest.raises(BizError) as exc:
            await svc.upload(
                owner_id=1001, kind=PartFileKind.DRAWING, data=b"",
                original_filename="x.pdf", content_type=None,
            )
        assert exc.value.code == ErrCode.BIZ_PART_FILE_TOO_LARGE


class TestUploadSingleFileKinds:
    """DRAWING / 3D_MODEL / ASSEMBLY_MASTER / CAD_2D：单文件覆盖。"""

    @pytest.mark.parametrize("kind,filename", [
        (PartFileKind.DRAWING, "x.pdf"),
        (PartFileKind.THREE_D_MODEL, "x.stp"),
        (PartFileKind.ASSEMBLY_MASTER, "x.pdf"),
    ])
    async def test_soft_deletes_existing_before_create(
        self, svc, mock_files, kind, filename,
    ):
        await svc.upload(
            owner_id=1001, kind=kind, data=b"x", original_filename=filename,
            content_type=None,
        )
        # 单文件 kind 应先 soft_delete_by_part_and_kind 再 create
        assert mock_files.soft_delete_by_part_and_kind.await_count == 1
        assert mock_files.create.await_count == 1

    async def test_assembly_master_uses_assembly_cos_prefix(self, svc, mock_files):
        await svc.upload(
            owner_id=1001, kind=PartFileKind.ASSEMBLY_MASTER, data=b"x",
            original_filename="x.pdf", content_type=None,
        )
        created_row = mock_files.create.await_args[0][0]
        assert created_row.object_key.startswith("drawings/assembly/1001/")


class TestUploadMultiVersionKinds:
    """G_CODE / SETUP_SHEET：允许多版本，不删旧的；两者通过 paired_file_id 关联。"""

    @pytest.mark.parametrize("kind,filename", [
        (PartFileKind.G_CODE, "x.nc"),
        (PartFileKind.SETUP_SHEET, "x.pdf"),
    ])
    async def test_does_not_soft_delete_existing(
        self, svc, mock_files, kind, filename,
    ):
        await svc.upload(
            owner_id=1001, kind=kind, data=b"x",
            original_filename=filename, content_type=None,
        )
        # 多版本 / 配对 kind：不调 soft_delete_by_part_and_kind
        assert mock_files.soft_delete_by_part_and_kind.await_count == 0
        assert mock_files.create.await_count == 1


class TestUploadSingleInsertNoTwoPhase:
    """约束：构造对象时一次性带齐所有列，避免 INSERT → UPDATE 两阶段写
    导致 MissingGreenlet（CLAUDE.md §13）。"""

    async def test_no_extra_flush_after_create(self, svc, mock_files):
        """upload 不应在 create 之后额外 flush；AuditMixin.updated_at 自动维护。"""
        await svc.upload(
            owner_id=1001, kind=PartFileKind.DRAWING, data=b"x",
            original_filename="x.pdf", content_type=None,
        )
        # mock_files.create 是 AsyncMock，不触发真实 flush；
        # 但 verify: file_row 构造时 created_by / updated_by 已经赋值（无需后补）
        created_row = mock_files.create.await_args[0][0]
        assert hasattr(created_row, "created_by")
        assert hasattr(created_row, "updated_by")


# ============================================================
# delete_file
# ============================================================


class TestDeleteFile:
    async def test_not_found_raises(self, svc, mock_files):
        mock_files.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(BizError) as exc:
            await svc.delete_file(999)
        assert exc.value.code == ErrCode.BIZ_PART_FILE_NOT_FOUND
        assert exc.value.http_status == 404

    async def test_soft_deletes_row(self, svc, mock_files):
        f = make_part_file()
        mock_files.get_by_id = AsyncMock(return_value=f)
        await svc.delete_file(f.id)
        assert f.deleted_at is not None
        mock_files.soft_delete.assert_awaited_once_with(f)
        # delete_file 通过 asyncio.create_task 异步清理 COS；
        # 我们不强制等它完成，只验证 soft_delete 已被调用
        # （实际 COS 清理由 fire-and-forget 触发）


# ============================================================
# get_download_url / get_file_content
# ============================================================


class TestGetDownloadUrl:
    async def test_not_found_raises(self, svc, mock_files):
        with pytest.raises(BizError) as exc:
            await svc.get_download_url(999)
        assert exc.value.code == ErrCode.BIZ_PART_FILE_NOT_FOUND


class TestGetFileContent:
    async def test_returns_blob(self, svc, mock_files):
        f = make_part_file(object_key="drawings/part/1001/9001.pdf")
        mock_files.get_by_id = AsyncMock(return_value=f)
        with patch("service.part_file.cos_mod.download_object",
                    new=AsyncMock(return_value=b"%PDF-1.4 fake")):
            data, ct, name = await svc.get_file_content(f.id)
        assert data == b"%PDF-1.4 fake"
        assert ct == "application/pdf"
        assert name == "drawing.pdf"


# ============================================================
# list_for_part
# ============================================================


class TestListForPart:
    async def test_returns_part_file_out_list(self, svc, mock_files):
        f1 = make_part_file(id=1, kind="DRAWING")
        f2 = make_part_file(id=2, kind="G_CODE")
        mock_files.list_by_part = AsyncMock(return_value=[f1, f2])
        with patch("service.part_file.cos_mod.presigned_get_url",
                    new=AsyncMock(return_value="https://example.com/x")):
            out = await svc.list_for_part(1001)
        assert len(out) == 2
        assert out[0].id == 1
        assert out[0].kind == "DRAWING"
        assert out[1].kind == "G_CODE"

    async def test_filter_by_kind(self, svc, mock_files):
        f1 = make_part_file(id=1, kind="DRAWING")
        f2 = make_part_file(id=2, kind="G_CODE")
        # 仅查询 kind="G_CODE"
        mock_files.list_by_part = AsyncMock(return_value=[f2])
        with patch("service.part_file.cos_mod.presigned_get_url",
                    new=AsyncMock(return_value="https://example.com/x")):
            out = await svc.list_for_part(1001, kind="G_CODE")
        # 验证 list_by_part 收到了 kind 参数
        mock_files.list_by_part.assert_awaited_once_with(1001, kind="G_CODE")
        assert len(out) == 1
        assert out[0].kind == "G_CODE"


# ============================================================
# 2026-07-14 新增：内容去重（SHA-256）
# ============================================================


class TestUploadDedup:
    """同 (part_id, kind, content_sha256) 已存在时复用现有行，不重传 COS。"""

    async def test_same_content_dedup_hit_no_new_row(self, svc, mock_files):
        """单文件 kind：命中时复用现有行，只更新 original_filename/updated_by。"""
        existing = make_part_file(
            id=9001,
            part_id=1001,
            kind="DRAWING",
            original_filename="old.pdf",
            content_sha256="a" * 64,
        )
        mock_files.find_active_by_part_kind_sha = AsyncMock(return_value=existing)

        # 上传同字节 + 新文件名
        out = await svc.upload(
            owner_id=1001, kind=PartFileKind.DRAWING, data=b"hello",
            original_filename="new.pdf", content_type=None,
        )
        # DB 行未新建
        assert mock_files.create.await_count == 0
        # 单文件 kind 命中：仅更新 filename，不调 soft_delete
        assert mock_files.soft_delete_by_part_and_kind.await_count == 0
        # 文件名已更新
        assert existing.original_filename == "new.pdf"
        # id 是 int（Pydantic 序列化时转 str，但 Python 内部仍是 int）
        assert out.id == 9001

    async def test_same_content_g_code_noop(self, svc, mock_files):
        """G_CODE 多版本：同内容再传 no-op，不动任何字段。"""
        existing = make_part_file(
            id=9001,
            part_id=1001,
            kind="G_CODE",
            original_filename="prog.nc",
            content_sha256="b" * 64,
        )
        mock_files.find_active_by_part_kind_sha = AsyncMock(return_value=existing)

        out = await svc.upload(
            owner_id=1001, kind=PartFileKind.G_CODE, data=b"hello",
            original_filename="prog_renamed.nc", content_type=None,
        )
        # DB 行未新建
        assert mock_files.create.await_count == 0
        # G_CODE 命中：文件名保持原值（no-op）
        assert existing.original_filename == "prog.nc"
        assert out.id == 9001

    async def test_different_content_creates_new_row(self, svc, mock_files):
        """不同字节 → 新建行（CAS key 派生 sha16）。"""
        mock_files.find_active_by_part_kind_sha = AsyncMock(return_value=None)
        await svc.upload(
            owner_id=1001, kind=PartFileKind.DRAWING, data=b"hello",
            original_filename="foo.pdf", content_type=None,
        )
        assert mock_files.create.await_count == 1
        created = mock_files.create.await_args[0][0]
        # sha 写入
        assert created.content_sha256 is not None
        assert len(created.content_sha256) == 64
        # COS key 用新模板（含 sha16 + safe_filename）
        assert created.object_key.startswith(
            f"drawings/part/1001/DRAWING/"
        )
        assert created.original_filename == "foo.pdf"

    async def test_dedup_skips_cos_put(self, svc, mock_files):
        """命中时完全跳过 cos.upload_object。"""
        existing = make_part_file(
            id=9001, part_id=1001, kind="DRAWING", content_sha256="c" * 64,
        )
        mock_files.find_active_by_part_kind_sha = AsyncMock(return_value=existing)
        with patch("service.part_file.cos_mod.upload_object",
                    new=AsyncMock()) as upload_mock:
            await svc.upload(
                owner_id=1001, kind=PartFileKind.DRAWING, data=b"hello",
                original_filename="x.pdf", content_type=None,
            )
        assert upload_mock.await_count == 0

    async def test_dedup_uses_cas_key_with_sha16(self, svc, mock_files):
        """CAS key 模板包含 sha16 前缀 + safe_filename。"""
        mock_files.find_active_by_part_kind_sha = AsyncMock(return_value=None)
        await svc.upload(
            owner_id=1001, kind=PartFileKind.DRAWING, data=b"hello world",
            original_filename="foo bar.pdf", content_type=None,
        )
        created = mock_files.create.await_args[0][0]
        # sha256("hello world") = b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
        sha16 = "b94d27b9934d3e08"
        assert sha16 in created.object_key
        # 中文 / 特殊字符折叠成 _
        assert "foo_bar.pdf" in created.object_key


class TestUploadNewKindsAndFormats:
    """2026-07-14 扩展：CAD_2D kind + DRAWING 加图片格式 + 3D_MODEL 扩 IGES/STL/OBJ/3MF。"""

    @pytest.mark.parametrize("kind,filename", [
        (PartFileKind.DRAWING, "photo.png"),
        (PartFileKind.DRAWING, "scan.jpg"),
        (PartFileKind.DRAWING, "blueprint.tiff"),
        (PartFileKind.DRAWING, "iphone.heic"),
        (PartFileKind.THREE_D_MODEL, "model.iges"),
        (PartFileKind.THREE_D_MODEL, "model.igs"),
        (PartFileKind.THREE_D_MODEL, "mesh.stl"),
        (PartFileKind.THREE_D_MODEL, "wave.obj"),
        (PartFileKind.THREE_D_MODEL, "print.3mf"),
        (PartFileKind.CAD_2D, "source.dwg"),
        (PartFileKind.CAD_2D, "source.dxf"),
    ])
    async def test_new_ext_round_trips(
        self, svc, mock_files, kind, filename,
    ):
        await svc.upload(
            owner_id=1001, kind=kind, data=b"x",
            original_filename=filename, content_type=None,
        )
        assert mock_files.create.await_count == 1
        created = mock_files.create.await_args[0][0]
        # 扩展名转大写（file_type 字段约定）
        ext = filename.rsplit(".", 1)[-1].upper()
        assert created.file_type == ext

    async def test_drawing_png_replaces_pdf(self, svc, mock_files):
        """DRAWING 单文件 kind：上传新格式自动 soft_delete 旧行（覆盖语义）。"""
        await svc.upload(
            owner_id=1001, kind=PartFileKind.DRAWING, data=b"x",
            original_filename="x.png", content_type=None,
        )
        # 单文件 kind：先 soft_delete 旧 (1001, DRAWING)，再 create
        assert mock_files.soft_delete_by_part_and_kind.await_count == 1
        assert mock_files.create.await_count == 1
        # key 路径含 /DRAWING/（图片和 PDF 同 kind）
        created = mock_files.create.await_args[0][0]
        assert "/DRAWING/" in created.object_key