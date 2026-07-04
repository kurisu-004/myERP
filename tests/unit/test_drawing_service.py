"""Unit tests for DrawingService."""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TDrawingFile
from repository.assembly import AssemblyRepository
from repository.drawing_file import DrawingFileRepository
from repository.part import PartRepository
from service.drawing import DrawingService

pytestmark = pytest.mark.asyncio


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def mock_files_repo() -> DrawingFileRepository:
    """DrawingFileRepository with all methods mocked as AsyncMock."""
    repo = DrawingFileRepository.__new__(DrawingFileRepository)
    repo.list_by_part = AsyncMock()
    repo.list_by_assembly = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.create = AsyncMock()
    repo.soft_delete = AsyncMock()
    return repo


@pytest.fixture
def mock_parts_repo() -> PartRepository:
    """PartRepository with get_by_id mocked as AsyncMock."""
    repo = PartRepository.__new__(PartRepository)
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def mock_assemblies_repo() -> AssemblyRepository:
    """AssemblyRepository with get_by_id mocked as AsyncMock."""
    repo = AssemblyRepository.__new__(AssemblyRepository)
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def service(
    mock_files_repo: DrawingFileRepository,
    mock_parts_repo: PartRepository,
    mock_assemblies_repo: AssemblyRepository,
) -> DrawingService:
    """DrawingService backed by mock repositories."""
    return DrawingService(
        files=mock_files_repo,
        parts=mock_parts_repo,
        assemblies=mock_assemblies_repo,
    )


def _make_file(
    id: int = 1001,
    part_id: int | None = 1,
    assembly_id: int | None = None,
    file_type: str = "PDF",
    object_key: str = "drawings/part/1/1001.pdf",
    original_filename: str = "drawing.pdf",
    file_size: int = 1024,
    content_type: str = "application/pdf",
    page_index: int | None = None,
    upload_status: str = "READY",
    created_at: datetime | None = None,
) -> TDrawingFile:
    """Factory helper to construct a TDrawingFile-like object.

    Uses MagicMock(spec=TDrawingFile) as a lightweight data container
    that avoids SQLAlchemy descriptor issues when bypassing a DB session.
    """
    f = MagicMock(spec=TDrawingFile)
    f.id = id
    f.part_id = part_id
    f.assembly_id = assembly_id
    f.file_type = file_type
    f.object_key = object_key
    f.original_filename = original_filename
    f.file_size = file_size
    f.content_type = content_type
    f.page_index = page_index
    f.upload_status = upload_status
    f.created_at = created_at or datetime(2026, 7, 1, 12, 0, 0)
    return f


# ======================================================================
# list_for_part
# ======================================================================


class TestListForPart:
    """Tests for DrawingService.list_for_part."""

    async def test_happy_path(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
        mock_parts_repo: PartRepository,
    ) -> None:
        """list_for_part validates part, lists files, and signs each URL."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts_repo.get_by_id.return_value = object()  # any truthy sentinel
        f1 = _make_file(id=1, part_id=10, object_key="k1.pdf")
        f2 = _make_file(id=2, part_id=10, object_key="k2.pdf")
        mock_files_repo.list_by_part.return_value = [f1, f2]

        # ── act ──────────────────────────────────────────────────
        with patch(
            "core.cos.presigned_get_url",
            new=AsyncMock(side_effect=["https://url/k1", "https://url/k2"]),
        ) as mock_url:
            result = await service.list_for_part(10)

        # ── assert ───────────────────────────────────────────────
        mock_parts_repo.get_by_id.assert_awaited_once_with(10)
        mock_files_repo.list_by_part.assert_awaited_once_with(10)
        assert mock_url.await_count == 2
        assert len(result) == 2
        assert result[0].download_url == "https://url/k1"
        assert result[1].download_url == "https://url/k2"

    async def test_part_not_found(
        self,
        service: DrawingService,
        mock_parts_repo: PartRepository,
    ) -> None:
        """list_for_part raises 404 when the part does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.list_for_part(99)

        mock_parts_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert "part 99" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# list_for_assembly
# ======================================================================


class TestListForAssembly:
    """Tests for DrawingService.list_for_assembly."""

    async def test_happy_path(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
        mock_assemblies_repo: AssemblyRepository,
    ) -> None:
        """list_for_assembly validates assembly, lists files, and signs URLs."""
        # ── arrange ──────────────────────────────────────────────
        mock_assemblies_repo.get_by_id.return_value = object()
        f1 = _make_file(id=1, assembly_id=20, object_key="k1.pdf")
        mock_files_repo.list_by_assembly.return_value = [f1]

        # ── act ──────────────────────────────────────────────────
        with patch(
            "core.cos.presigned_get_url",
            new=AsyncMock(return_value="https://url/k1"),
        ):
            result = await service.list_for_assembly(20)

        # ── assert ───────────────────────────────────────────────
        mock_assemblies_repo.get_by_id.assert_awaited_once_with(20)
        mock_files_repo.list_by_assembly.assert_awaited_once_with(20)
        assert len(result) == 1
        assert result[0].download_url == "https://url/k1"

    async def test_assembly_not_found(
        self,
        service: DrawingService,
        mock_assemblies_repo: AssemblyRepository,
    ) -> None:
        """list_for_assembly raises 404 when the assembly does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_assemblies_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.list_for_assembly(99)

        mock_assemblies_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert "assembly 99" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# get_download_url
# ======================================================================


class TestGetDownloadUrl:
    """Tests for DrawingService.get_download_url."""

    async def test_happy_path(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
    ) -> None:
        """get_download_url returns a presigned URL for an existing file."""
        # ── arrange ──────────────────────────────────────────────
        f = _make_file(id=5, object_key="some/key.pdf")
        mock_files_repo.get_by_id.return_value = f

        # ── act ──────────────────────────────────────────────────
        with patch(
            "core.cos.presigned_get_url",
            new=AsyncMock(return_value="https://dl.url/file"),
        ) as mock_url:
            url = await service.get_download_url(5)

        # ── assert ───────────────────────────────────────────────
        mock_files_repo.get_by_id.assert_awaited_once_with(5)
        mock_url.assert_awaited_once_with("some/key.pdf")
        assert url == "https://dl.url/file"

    async def test_file_not_found(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
    ) -> None:
        """get_download_url raises 404 when the file does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_files_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.get_download_url(99)

        mock_files_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_NOT_FOUND
        assert "file 99" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# get_file_content
# ======================================================================


class TestGetFileContent:
    """Tests for DrawingService.get_file_content."""

    async def test_happy_path(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
    ) -> None:
        """get_file_content returns (data, content_type, filename) for existing file."""
        # ── arrange ──────────────────────────────────────────────
        f = _make_file(
            id=5,
            object_key="some/key.pdf",
            original_filename="report.pdf",
            content_type="application/pdf",
        )
        mock_files_repo.get_by_id.return_value = f

        # ── act ──────────────────────────────────────────────────
        with patch(
            "core.cos.download_object",
            new=AsyncMock(return_value=b"pdf-content"),
        ) as mock_dl:
            data, ct, filename = await service.get_file_content(5)

        # ── assert ───────────────────────────────────────────────
        mock_files_repo.get_by_id.assert_awaited_once_with(5)
        mock_dl.assert_awaited_once_with("some/key.pdf")
        assert data == b"pdf-content"
        assert ct == "application/pdf"
        assert filename == "report.pdf"

    async def test_file_not_found(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
    ) -> None:
        """get_file_content raises 404 when the file does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_files_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.get_file_content(99)

        mock_files_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_NOT_FOUND
        assert "file 99" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# delete_file
# ======================================================================


class TestDeleteFile:
    """Tests for DrawingService.delete_file."""

    async def test_happy_path(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
    ) -> None:
        """delete_file soft-deletes the row and schedules fire-and-forget COS delete."""
        # ── arrange ──────────────────────────────────────────────
        f = _make_file(id=5, object_key="some/key.pdf")
        mock_files_repo.get_by_id.return_value = f

        # ── act ──────────────────────────────────────────────────
        with patch("core.cos.delete_object", new=AsyncMock()) as mock_del:
            await service.delete_file(5)
            # Yield control so the fire-and-forget background task runs
            await asyncio.sleep(0)

        # ── assert ───────────────────────────────────────────────
        mock_files_repo.get_by_id.assert_awaited_once_with(5)
        mock_files_repo.soft_delete.assert_awaited_once_with(f)

        # COS delete was NOT awaited synchronously inside delete_file —
        # it was scheduled as a fire-and-forget task via asyncio.create_task.
        # We let the task run with asyncio.sleep(0) above, so now we verify
        # the coroutine was called and awaited in the background.
        mock_del.assert_awaited_once_with("some/key.pdf")

    async def test_file_not_found(
        self,
        service: DrawingService,
        mock_files_repo: DrawingFileRepository,
    ) -> None:
        """delete_file raises 404 when the file does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_files_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.delete_file(99)

        mock_files_repo.get_by_id.assert_awaited_once_with(99)
        mock_files_repo.soft_delete.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_NOT_FOUND
        assert "file 99" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# delete_files_silently
# ======================================================================


class TestDeleteFilesSilently:
    """Tests for DrawingService.delete_files_silently."""

    async def test_empty_list_noop(self, service: DrawingService) -> None:
        """delete_files_silently is a no-op when given an empty list."""
        # ── arrange ──────────────────────────────────────────────
        # nothing to arrange

        # ── act ──────────────────────────────────────────────────
        with patch("core.cos.delete_object", new=AsyncMock()) as mock_del:
            result = await service.delete_files_silently([])

        # ── assert ───────────────────────────────────────────────
        assert result is None
        mock_del.assert_not_called()


# ======================================================================
# upload_to_part
# ======================================================================


class TestUploadToPart:
    """Tests for DrawingService.upload_to_part."""

    async def test_part_not_found(
        self,
        service: DrawingService,
        mock_parts_repo: PartRepository,
    ) -> None:
        """upload_to_part raises 404 when the referenced part does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("core.cos.upload_object", new=AsyncMock()),
            patch("utils.id_gen.new_id", return_value=2001),
        ):
            with pytest.raises(BizError) as exc_info:
                await service.upload_to_part(
                    99,
                    data=b"content",
                    original_filename="test.pdf",
                    content_type="application/pdf",
                )

        mock_parts_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_PART_NOT_FOUND
        assert "part 99" in exc_info.value.message
        assert exc_info.value.http_status == 404

    async def test_empty_file_raises(
        self,
        service: DrawingService,
        mock_parts_repo: PartRepository,
    ) -> None:
        """upload_to_part raises 400 when data is empty (size <= 0)."""
        # ── arrange ──────────────────────────────────────────────
        mock_parts_repo.get_by_id.return_value = object()  # part exists

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("core.cos.upload_object", new=AsyncMock()),
            patch("utils.id_gen.new_id", return_value=2001),
        ):
            with pytest.raises(BizError) as exc_info:
                await service.upload_to_part(
                    10,
                    data=b"",
                    original_filename="test.pdf",
                    content_type="application/pdf",
                )

        mock_parts_repo.get_by_id.assert_awaited_once_with(10)
        assert exc_info.value.code == ErrCode.BIZ_DRAWING_FILE_TOO_LARGE
        assert "empty file" in exc_info.value.message
        assert exc_info.value.http_status == 400


# ======================================================================
# upload_to_assembly
# ======================================================================


class TestUploadToAssembly:
    """Tests for DrawingService.upload_to_assembly."""

    async def test_assembly_not_found(
        self,
        service: DrawingService,
        mock_assemblies_repo: AssemblyRepository,
    ) -> None:
        """upload_to_assembly raises 404 when the referenced assembly does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_assemblies_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with (
            patch("core.cos.upload_object", new=AsyncMock()),
            patch("utils.id_gen.new_id", return_value=2001),
        ):
            with pytest.raises(BizError) as exc_info:
                await service.upload_to_assembly(
                    99,
                    data=b"content",
                    original_filename="test.pdf",
                    content_type="application/pdf",
                )

        mock_assemblies_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_ASSEMBLY_NOT_FOUND
        assert "assembly 99" in exc_info.value.message
        assert exc_info.value.http_status == 404
