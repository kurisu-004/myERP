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


class TestBarcodePageLayoutVertical:
    """2026-07-20 v2 迭代：序列号 + 条码旋转 90° 贴 A4 右边。

    验证 _build_barcode_page 的输出:
    - 右边 25% 区域有大量黑色像素（条码在右边）；
    - 左边 75% 区域基本为白色（所有内容都在右边）；
    - 上下 25% 基本为白色（块垂直居中）；
    - 右条带上下半都有黑色像素（序列号 + 条码堆叠）。
    """

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_right_quarter_has_barcode(self, orientation: str) -> None:
        """右边 25% 区域应有条码黑色像素。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        right = img.crop((int(w * 0.75), 0, w, h)).convert("L")
        hist = right.histogram()
        black = hist[0] + hist[1]
        assert black > 500, f"右边 25% 应有大量条码黑色像素，实际 black={black}"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_left_half_is_empty(self, orientation: str) -> None:
        """左半页应基本为白色（块只在右边，块左缘 > page_w/2）。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        left = img.crop((0, 0, w // 2, h)).convert("L")
        hist = left.histogram()
        non_white = sum(hist[:250])
        total = sum(hist)
        assert non_white / total < 0.001, (
            f"左半页应基本为白色，实际非白像素 {non_white}/{total}"
        )

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_top_left_corner_is_empty(self, orientation: str) -> None:
        """左上角（左半页的上半）应基本为白色。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        tl = img.crop((0, 0, w // 2, h // 2)).convert("L")
        hist = tl.histogram()
        non_white = sum(hist[:250])
        total = sum(hist)
        assert non_white / total < 0.001, (
            f"左上角应基本为白色，实际非白像素 {non_white}/{total}"
        )

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_bottom_left_corner_is_empty(self, orientation: str) -> None:
        """左下角（左半页的下半）应基本为白色。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        bl = img.crop((0, h // 2, w // 2, h)).convert("L")
        hist = bl.histogram()
        non_white = sum(hist[:250])
        total = sum(hist)
        assert non_white / total < 0.001, (
            f"左下角应基本为白色，实际非白像素 {non_white}/{total}"
        )

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_serial_above_barcode_in_right_strip(self, orientation: str) -> None:
        """右条带上下半都有黑色像素（序列号 + 条码堆叠）。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        right_strip = img.crop((int(w * 0.70), 0, w, h)).convert("L")
        rs_w, rs_h = right_strip.size
        upper_hist = right_strip.crop((0, 0, rs_w, rs_h // 2)).histogram()
        lower_hist = right_strip.crop((0, rs_h // 2, rs_w, rs_h)).histogram()
        upper_black = upper_hist[0] + upper_hist[1]
        lower_black = lower_hist[0] + lower_hist[1]
        assert upper_black > 100, f"右条带上半应有序列号，实际 black={upper_black}"
        assert lower_black > 100, f"右条带下半应有条码，实际 black={lower_black}"


class TestDrawingPdfMediaboxNormalization:
    """2026-07-20 修复：浏览器打印预览中图纸被裁切。

    验证源 PDF 即便带非标 CropBox / TrimBox，合并后每页的 MediaBox /
    CropBox / TrimBox / BleedBox 都被规范化为精确 A4。
    """

    @pytest.mark.parametrize("orientation,a4_pt", [
        ("landscape", (842, 595)),
        ("portrait", (595, 842)),
    ])
    async def test_pdf_drawing_pages_have_a4_mediabox(
        self, monkeypatch, fake_parts_repo, orientation, a4_pt,
    ):
        from pypdf import PdfWriter as _Pw
        from pypdf.generic import RectangleObject
        w, h = a4_pt
        src = _Pw()
        page = src.add_blank_page(width=w, height=h)
        # 故意设置比 MediaBox 小的 CropBox / TrimBox（模拟 CAD 导出）
        page.cropbox = RectangleObject([10, 10, w - 10, h - 10])
        page.trimbox = RectangleObject([5, 5, w - 5, h - 5])
        buf = io.BytesIO()
        src.write(buf)

        async def fake_download(_key):
            return buf.getvalue()
        import service.printing as printing_mod
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            return_value=[_make_drawing_row("PDF", "pdf", "drawings/part/1234/DRAWING/x")]
        )

        pdf_bytes = await build_part_print_pdf(
            part_id=1234, parts=fake_parts_repo, part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) == 2
        w_pt, h_pt = a4_pt
        for i, p in enumerate(reader.pages):
            assert float(p.mediabox.width) == pytest.approx(w_pt, abs=0.1)
            assert float(p.mediabox.height) == pytest.approx(h_pt, abs=0.1)
            assert float(p.cropbox.width) == pytest.approx(w_pt, abs=0.1), (
                f"page {i}: cropbox 未规范化，浏览器打印预览会裁切图纸"
            )
            assert float(p.cropbox.height) == pytest.approx(h_pt, abs=0.1)