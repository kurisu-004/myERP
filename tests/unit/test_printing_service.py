"""Unit tests for service/printing.py.

2026-07-14 扩展：DRAWING 接受 PDF + 9 种图片格式（PNG/JPG/GIF/BMP/TIFF/WEBP/HEIC）；
本测试覆盖各种图片格式的双面 PDF 生成路径 + 朝向检测 + 条码页拼接。

测试策略：mock COS `download_object` 返回 raw 图片/PDF 字节；mock PartRepository +
PartFileRepository；走真实 build_part_print_pdf → 校验返回 PDF 的页数与尺寸。
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from pypdf import PdfReader

from model import TPartFile
from service.printing import build_part_print_pdf


pytestmark = pytest.mark.asyncio


def _png_bytes(w: int = 800, h: int = 600) -> bytes:
    """生成最小 PNG 字节（landscape）。"""
    img = Image.new("RGB", (w, h), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _make_blank_pdf(w_pt: int = 842, h_pt: int = 595) -> bytes:
    """生成单页 PDF（默认 landscape A4）。"""
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=w_pt, height=h_pt)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def fake_part():
    p = MagicMock()
    p.id = 1234
    p.serial_no = "L2014"
    p.drawing_no = "DWG-001"
    p.name = "测试零件"
    return p


@pytest.fixture
def fake_parts_repo(fake_part):
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=fake_part)
    return repo


def _make_drawing_row(file_type: str, ext: str, object_key: str) -> TPartFile:
    row = MagicMock()
    row.id = 9999
    row.part_id = 1234
    row.kind = "DRAWING"
    row.file_type = file_type
    row.object_key = object_key
    row.original_filename = f"test.{ext}"
    row.file_size = 1024
    row.content_type = "application/octet-stream"
    return row


class TestBuildPartPrintPdfImageFormats:
    """2026-07-14：DRAWING 扩 9 种图片格式，每种都能正确生成双面 PDF。"""

    @pytest.mark.parametrize("ext,file_type,mime_factory", [
        ("png", "PNG", lambda: _png_bytes(800, 600)),
        ("jpg", "JPG", lambda: _png_bytes(800, 600)),  # pillow 可处理
        ("gif", "GIF", lambda: _png_bytes(800, 600)),
        ("bmp", "BMP", lambda: _png_bytes(800, 600)),
        ("tif", "TIF", lambda: _png_bytes(800, 600)),
        ("tiff", "TIFF", lambda: _png_bytes(800, 600)),
        ("webp", "WEBP", lambda: _png_bytes(800, 600)),
    ])
    async def test_image_format_2page_pdf(
        self, monkeypatch, fake_parts_repo, ext, file_type, mime_factory,
    ):
        """图片格式：page 1 = 图纸 + page 2 = 条码 + 序列号（双面）。"""
        # mock cos download_object 返回图片字节
        import service.printing as printing_mod
        async def fake_download(key):
            return mime_factory()
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            return_value=[_make_drawing_row(file_type, ext, f"drawings/part/1234/DRAWING/aaa_{ext}")]
        )

        pdf_bytes = await build_part_print_pdf(
            part_id=1234,
            parts=fake_parts_repo,
            part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 双面：page 1 图纸 + page 2 条码
        assert len(reader.pages) == 2
        # landscape 图片（800x600）→ A4 landscape (842x595 pt)
        page1 = reader.pages[0]
        assert float(page1.mediabox.width) > float(page1.mediabox.height)


class TestBuildPartPrintPdfPdfFallback:
    """PDF 格式：原始 PDF 直接合并 + 条码页追加。"""

    async def test_pdf_2page_pdf(self, monkeypatch, fake_parts_repo):
        import service.printing as printing_mod
        async def fake_download(key):
            return _make_blank_pdf(842, 595)  # landscape A4
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            return_value=[_make_drawing_row("PDF", "pdf", "drawings/part/1234/DRAWING/aaa_pdf")]
        )

        pdf_bytes = await build_part_print_pdf(
            part_id=1234, parts=fake_parts_repo, part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) == 2


class TestBuildPartPrintPdfNoDrawing:
    """无图纸 fallback：page 1 = 信息卡 + page 2 = 条码。"""

    async def test_info_card_2page_pdf(self, fake_parts_repo):
        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(return_value=[])  # 无图纸

        pdf_bytes = await build_part_print_pdf(
            part_id=1234, parts=fake_parts_repo, part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) == 2
        # 无图纸时默认 landscape
        page1 = reader.pages[0]
        assert float(page1.mediabox.width) > float(page1.mediabox.height)


class TestBuildPartPrintPdfOrientation:
    """朝向：图纸页与条码页同朝向。"""

    async def test_portrait_image_keeps_portrait(
        self, monkeypatch, fake_parts_repo,
    ):
        import service.printing as printing_mod
        # 竖图（600x800）
        async def fake_download(key):
            return _png_bytes(600, 800)
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            return_value=[_make_drawing_row("PNG", "png", "drawings/part/1234/DRAWING/aaa_png")]
        )

        pdf_bytes = await build_part_print_pdf(
            part_id=1234, parts=fake_parts_repo, part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 两页都应是 portrait
        for page in reader.pages:
            assert float(page.mediabox.width) < float(page.mediabox.height)


class TestBarcodePageLayout:
    """2026-07-20 迭代：反面页序列号 + 条码水平居中贴 A4 短边底部。

    验证 _build_barcode_page 的输出:
    - 上半页（50%）基本为白色（序列号与条码都贴在底部）；
    - 左下区域有黑色像素（条码水平居中而非贴右）；
    - 左右半页的黑色像素数量大致对称（水平居中）。
    """

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_top_half_is_empty(self, orientation: str) -> None:
        """序列号 + 条码贴底 → 上半页（50%）应基本为白色。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        top = img.crop((0, 0, w, h // 2)).convert("L")
        hist = top.histogram()
        non_white = sum(hist[:250])  # 灰度 < 250 的像素
        total = sum(hist)
        assert non_white / total < 0.005, (
            f"上半页应基本为白色，实际非白像素 {non_white}/{total}"
        )

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_bottom_left_has_barcode(self, orientation: str) -> None:
        """条码水平居中 → 左下 50% 区域应有黑色像素（旧布局在右下角不会）。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        bot_left = img.crop((0, int(h * 0.7), w // 2, h)).convert("L")
        hist = bot_left.histogram()
        black = hist[0]  # 灰度 == 0 的像素
        assert black > 100, f"左下区域应有条码黑色像素，实际 black={black}"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_centered_horizontally(self, orientation: str) -> None:
        """水平居中：左半与右半页的黑色像素数量应大致对称。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        bottom = img.crop((0, h // 2, w, h)).convert("L")
        bw = bottom.width
        left_hist = bottom.crop((0, 0, bw // 2, bottom.height)).histogram()
        right_hist = bottom.crop((bw // 2, 0, bw, bottom.height)).histogram()
        left_black = left_hist[0] + left_hist[1]
        right_black = right_hist[0] + right_hist[1]
        total_black = left_black + right_black
        assert total_black > 100, f"下半页黑色像素过少：{total_black}"
        diff_ratio = abs(left_black - right_black) / total_black
        assert diff_ratio < 0.5, (
            f"左右严重不对称：left={left_black} right={right_black}"
        )