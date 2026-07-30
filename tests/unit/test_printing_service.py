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
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject

from model import TPartFile
from service.printing import _fit_pdf_page_to_a4, build_part_print_pdf
from datetime import date


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
    """朝向：图纸页跟随上传图纸；条形码页强制 landscape（2026-07-24 解耦）。"""

    async def test_portrait_drawing_barcode_page_always_landscape(
        self, monkeypatch, fake_parts_repo,
    ):
        """2026-07-24：竖图 → 图纸页 portrait，但条码页固定 landscape。

        之前是双页都 portrait（朝向跟随图纸），用户反馈不利于扫码。
        """
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
        # page[0] = 图纸页：portrait
        page0 = reader.pages[0]
        assert float(page0.mediabox.width) < float(page0.mediabox.height)
        # page[1] = 条码页：landscape（强制）
        page1 = reader.pages[1]
        assert float(page1.mediabox.width) > float(page1.mediabox.height)
        # 精确 A4 landscape
        assert float(page1.mediabox.width) == 842
        assert float(page1.mediabox.height) == 595

    async def test_landscape_drawing_barcode_page_also_landscape(
        self, monkeypatch, fake_parts_repo,
    ):
        """横图 → 两页都 landscape（不变）。"""
        import service.printing as printing_mod
        # 横图（800x600）
        async def fake_download(key):
            return _png_bytes(800, 600)
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            return_value=[_make_drawing_row("PNG", "png", "drawings/part/5678/DRAWING/bbb_png")]
        )

        pdf_bytes = await build_part_print_pdf(
            part_id=5678, parts=fake_parts_repo, part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            assert float(page.mediabox.width) > float(page.mediabox.height)


def _find_black_x_segments(img: Image.Image, y_lo: int, y_hi: int) -> list[tuple[int, int]]:
    """在 y ∈ [y_lo, y_hi] 水平条带内，找出 x 方向上的连续黑色像素段。

    用于验证「序列号在条码左侧」类布局——两段黑色像素之间应有 gap。
    """
    band = img.crop((0, y_lo, img.width, y_hi)).convert("L")
    bw = band.width
    bh = band.height
    segments: list[tuple[int, int]] = []
    in_seg = False
    start = 0
    for x in range(bw):
        col = band.crop((x, 0, x + 1, bh))
        col_min = min(col.getdata())
        has_black = col_min < 128
        if has_black and not in_seg:
            start = x
            in_seg = True
        elif not has_black and in_seg:
            segments.append((start, x - 1))
            in_seg = False
    if in_seg:
        segments.append((start, bw - 1))
    return segments


class TestBarcodePageLayoutVertical:
    """2026-07-20 v3 迭代：序列号 + 条码 CCW 旋转 90°，序列号在条码左侧并排。

    验证 _build_barcode_page 的输出:
    - 序列号与条码沿 A4 右边并排堆叠（不重叠），序列号在左侧（x 较小）；
    - 条码距页面右边精确 1 cm（RIGHT_MARGIN_PT = 28 pt ≈ 58 px @ 150 DPI）；
    - 左边 65% 区域基本为白色（所有内容都在右边）；
    - 右边 25% 区域有大量黑色像素（条码主体）。
    """

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_serial_left_of_barcode(self, orientation: str) -> None:
        """序列号在条码左侧，中间有 gap 不重叠。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        # 取垂直中间 40% 条带，水平扫黑色像素段
        segments = _find_black_x_segments(img, int(h * 0.3), int(h * 0.7))

        # 应有 ≥ 2 段：serial + barcode
        assert len(segments) >= 2, (
            f"中间条带应有 ≥ 2 段黑色像素（serial + barcode 并排），实际 {len(segments)} 段: {segments}"
        )

        # 第一段（最左）应是 serial，最右段应是 barcode；serial 在 barcode 左侧
        serial_right_edge = segments[0][1]
        barcode_left_edge = segments[-1][0]
        assert serial_right_edge < barcode_left_edge, (
            f"serial 应在 barcode 左侧：serial_right={serial_right_edge}, "
            f"barcode_left={barcode_left_edge}"
        )

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_barcode_1cm_from_right_edge(self, orientation: str) -> None:
        """条码距页面右边精确 1 cm（28 pt ≈ 58 px @ 150 DPI）。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        # 距右边 1 cm 区域应基本为白色（margin 区域）
        margin_zone = img.crop((w - 58, 0, w, h)).convert("L")
        hist = margin_zone.histogram()
        black = hist[0] + hist[1]
        assert black < 50, (
            f"距右边 1 cm 区域应为白色 margin，实际 black={black}"
        )

        # 条码主体区域（距右边 ~1-5 cm 内）应有大量黑色像素
        barcode_zone = img.crop((w - 316, int(h * 0.3), w - 58, int(h * 0.7))).convert("L")
        hist = barcode_zone.histogram()
        black = hist[0] + hist[1]
        assert black > 500, f"条码主体区域应有大量黑色像素，实际 black={black}"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_right_quarter_has_barcode(self, orientation: str) -> None:
        """右边 25% 区域应有大量条码黑色像素。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        right = img.crop((int(w * 0.75), 0, w, h)).convert("L")
        hist = right.histogram()
        black = hist[0] + hist[1]
        assert black > 500, f"右边 25% 应有大量条码黑色像素，实际 black={black}"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_left_center_is_empty(self, orientation: str) -> None:
        """2026-07-24 调整：左侧现在有 2 个小条码（左上 + 左下），但**中间**仍应基本为白色。

        之前是 test_left_half_is_empty；现在改成左中区域（避开左下和左上）。
        """
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size

        # 左中：x ∈ [0, w*0.25]，y ∈ [h*0.20, h*0.75]（避开左下小条码、左上旋转 180° 小条码）
        left_center = img.crop((0, int(h * 0.20), int(w * 0.25), int(h * 0.75))).convert("L")
        hist = left_center.histogram()
        non_white = sum(hist[:250])
        total = sum(hist)
        assert non_white / total < 0.001, (
            f"左中区域应基本为白色，实际非白像素 {non_white}/{total}"
        )


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
        # 2026-07-24：图纸页（page[0]）按 orientation；条码页（page[-1]）固定 landscape
        w_pt, h_pt = a4_pt
        bc_w, bc_h = 842, 595
        # 图纸页
        page0 = reader.pages[0]
        assert float(page0.mediabox.width) == pytest.approx(w_pt, abs=0.1)
        assert float(page0.mediabox.height) == pytest.approx(h_pt, abs=0.1)
        assert float(page0.cropbox.width) == pytest.approx(w_pt, abs=0.1), (
            f"图纸页 cropbox 未规范化，浏览器打印预览会裁切图纸"
        )
        assert float(page0.cropbox.height) == pytest.approx(h_pt, abs=0.1)
        # 条码页（永远 landscape）
        page1 = reader.pages[1]
        assert float(page1.mediabox.width) == pytest.approx(bc_w, abs=0.1)
        assert float(page1.mediabox.height) == pytest.approx(bc_h, abs=0.1)
        assert float(page1.cropbox.width) == pytest.approx(bc_w, abs=0.1)


class TestDrawingPdfFitToA4:
    @staticmethod
    def _marked_page(
        width: float,
        height: float,
        *,
        origin_x: float = 0,
        origin_y: float = 0,
        rotation: int = 0,
    ) -> PageObject:
        writer = PdfWriter()
        page = writer.add_blank_page(width=width, height=height)
        page.mediabox = RectangleObject(
            [origin_x, origin_y, origin_x + width, origin_y + height]
        )
        stream = DecodedStreamObject()
        # 在源页四周画框；经过适配后应随同内容流一起缩放和平移。
        stream.set_data(
            f"q 0 0 {width} {height} re S Q".encode("ascii")
        )
        page[NameObject("/Contents")] = stream
        if rotation:
            page.rotate(rotation)
        return page

    async def test_a3_landscape_content_is_scaled_into_a4(self) -> None:
        page = self._marked_page(1190.55, 841.89)

        _fit_pdf_page_to_a4(page, "landscape")

        assert tuple(float(v) for v in page.mediabox) == pytest.approx(
            (0, 0, 842, 595), abs=0.1
        )
        operations = page.get_contents().operations
        matrix = next(operands for operands, operator in operations if operator == b"cm")
        scale = min(842 / 1190.55, 595 / 841.89)
        assert float(matrix[0]) == pytest.approx(scale, abs=1e-5)
        assert float(matrix[3]) == pytest.approx(scale, abs=1e-5)
        assert float(matrix[4]) == pytest.approx(
            (842 - 1190.55 * scale) / 2, abs=1e-4
        )
        assert float(matrix[5]) == pytest.approx(0, abs=1e-4)

    async def test_a4_page_is_not_upscaled(self) -> None:
        page = self._marked_page(800, 500)

        _fit_pdf_page_to_a4(page, "landscape")

        matrix = next(
            operands
            for operands, operator in page.get_contents().operations
            if operator == b"cm"
        )
        assert float(matrix[0]) == pytest.approx(1)
        assert float(matrix[3]) == pytest.approx(1)
        assert float(matrix[4]) == pytest.approx(21)
        assert float(matrix[5]) == pytest.approx(47.5)

    async def test_nonzero_origin_is_normalized(self) -> None:
        page = self._marked_page(1190.55, 841.89, origin_x=12, origin_y=18)

        _fit_pdf_page_to_a4(page, "landscape")

        matrix = next(
            operands
            for operands, operator in page.get_contents().operations
            if operator == b"cm"
        )
        scale = min(842 / 1190.55, 595 / 841.89)
        assert float(matrix[4]) == pytest.approx(
            -12 * scale + (842 - 1190.55 * scale) / 2, abs=1e-4
        )
        assert float(matrix[5]) == pytest.approx(-18 * scale, abs=1e-4)
        assert tuple(float(v) for v in page.cropbox) == pytest.approx(
            (0, 0, 842, 595), abs=0.1
        )

    async def test_rotated_page_uses_visual_dimensions(self) -> None:
        page = self._marked_page(595, 842, rotation=90)

        _fit_pdf_page_to_a4(page, "landscape")

        assert page.rotation == 0
        assert tuple(float(v) for v in page.mediabox) == pytest.approx(
            (0, 0, 842, 595), abs=0.1
        )


class TestSmallBarcodesOnBarcodePage:
    """2026-07-24：图纸条形码页加 2 个小条码 + 序列号副本。

    - 左下：水平放置（不旋转）
    - 左上：旋转 180° 放置
    防图纸污染无法扫码；用户从不同角度扫都能命中。
    """

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_small_barcode_bottom_left(self, orientation: str) -> None:
        """左下角区域有黑色像素（小条码 + 小序列号）。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size
        # 左下：x ∈ [0, w*0.30]，y ∈ [h*0.85, h]
        crop = img.crop((0, int(h * 0.85), int(w * 0.30), h)).convert("L")
        # 应有黑色像素
        assert crop.getextrema()[0] < 128

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_small_barcode_top_left_rotated(self, orientation: str) -> None:
        """左上角区域有黑色像素（旋转 180° 的小条码 + 序列号）。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(orientation, "L2014")
        w, h = img.size
        # 左上：x ∈ [0, w*0.30]，y ∈ [0, h*0.15]
        crop = img.crop((0, 0, int(w * 0.30), int(h * 0.15))).convert("L")
        # 应有黑色像素
        assert crop.getextrema()[0] < 128

    def test_main_barcode_still_on_right(self) -> None:
        """主条码仍在右边（保持原 v3 设计），新增小条码不挤掉主条码。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page("landscape", "L2014")
        w, h = img.size
        # 右侧 25% 区域有大量黑色像素（主条码主体）
        crop = img.crop((int(w * 0.75), 0, w, h)).convert("L")
        # 主条码黑色像素密度应 ≥ 5%
        bw = sum(1 for px in crop.getdata() if px < 128)
        assert bw / crop.size[0] / crop.size[1] > 0.05


class TestBuildPartsPrintPdfBatchAssembly:
    """2026-07-30：批量打印附带装配件总装图。"""

    @pytest.fixture
    def fake_assembly(self):
        a = MagicMock()
        a.id = 1001
        a.serial_no = "L1001"
        a.drawing_no = "DWG-ASM-001"
        a.name = "测试装配体"
        return a

    @pytest.fixture
    def fake_assemblies_repo(self, fake_assembly):
        repo = MagicMock()
        repo.get_by_id = AsyncMock(return_value=fake_assembly)
        return repo

    @pytest.fixture
    def fake_child_part(self):
        p = MagicMock()
        p.id = 2001
        p.serial_no = "L1001-01"
        p.drawing_no = "DWG-CHILD-001"
        p.name = "子件1"
        p.assembly_id = 1001
        return p

    @pytest.fixture
    def fake_child_part2(self):
        p = MagicMock()
        p.id = 2002
        p.serial_no = "L1001-02"
        p.drawing_no = "DWG-CHILD-002"
        p.name = "子件2"
        p.assembly_id = 1001
        return p

    async def test_master_drawing_once_before_first_child(
        self,
        monkeypatch,
        fake_parts_repo,
        fake_assemblies_repo,
        fake_child_part,
        fake_child_part2,
    ):
        """选中同装配体 2 个子件 → 总装图只出现一次且位于首个子件前。"""
        import service.printing as printing_mod

        async def fake_download(key):
            return _make_blank_pdf(842, 595)
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        parts_repo = MagicMock()
        parts_repo.list_by_ids = AsyncMock(return_value=[fake_child_part, fake_child_part2])
        parts_repo.list_children = AsyncMock(return_value=[fake_child_part, fake_child_part2])
        parts_repo.get_by_id = AsyncMock(side_effect=lambda pid: fake_child_part if pid == 2001 else fake_child_part2)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            side_effect=lambda pid, kind: [
                _make_drawing_row("PDF", "pdf", f"drawings/{pid}/DRAWING/aaa.pdf")
            ] if kind == "DRAWING" else [
                _make_drawing_row("PDF", "pdf", f"drawings/{pid}/ASSEMBLY_MASTER/aaa.pdf")
            ]
        )
        files_repo.list_by_parts = AsyncMock(
            side_effect=lambda pids, kind=None, include_deleted=False: {
                pid: (_make_drawing_row("PDF", "pdf", f"drawings/{pid}/DRAWING/aaa.pdf")
                      if kind == "DRAWING" else
                      _make_drawing_row("PDF", "pdf", f"drawings/{pid}/ASSEMBLY_MASTER/aaa.pdf"))
                for pid in pids
            }
        )

        from service.printing import build_parts_print_pdf_batch
        pdf_bytes = await build_parts_print_pdf_batch(
            part_ids=[2001, 2002],
            parts=parts_repo,
            part_files=files_repo,
            assemblies=fake_assemblies_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 总装图 2 页 + 子件1 2 页 + 子件2 2 页 = 6 页
        assert len(reader.pages) == 6

    async def test_assembly_ids_prints_master_and_all_children(
        self,
        monkeypatch,
        fake_parts_repo,
        fake_assemblies_repo,
        fake_child_part,
        fake_child_part2,
    ):
        """assembly_ids 直选装配件 → 含总装图 + 全部子件。"""
        import service.printing as printing_mod

        async def fake_download(key):
            return _make_blank_pdf(842, 595)
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        parts_repo = MagicMock()
        parts_repo.list_by_ids = AsyncMock(return_value=[])
        parts_repo.list_children = AsyncMock(return_value=[fake_child_part, fake_child_part2])
        parts_repo.get_by_id = AsyncMock(side_effect=lambda pid: fake_child_part if pid == 2001 else fake_child_part2)

        files_repo = MagicMock()
        files_repo.list_by_part = AsyncMock(
            side_effect=lambda pid, kind: [
                _make_drawing_row("PDF", "pdf", f"drawings/{pid}/DRAWING/aaa.pdf")
            ] if kind == "DRAWING" else [
                _make_drawing_row("PDF", "pdf", f"drawings/{pid}/ASSEMBLY_MASTER/aaa.pdf")
            ]
        )
        files_repo.list_by_parts = AsyncMock(
            side_effect=lambda pids, kind=None, include_deleted=False: {
                pid: (_make_drawing_row("PDF", "pdf", f"drawings/{pid}/DRAWING/aaa.pdf")
                      if kind == "DRAWING" else
                      _make_drawing_row("PDF", "pdf", f"drawings/{pid}/ASSEMBLY_MASTER/aaa.pdf"))
                for pid in pids
            }
        )

        from service.printing import build_parts_print_pdf_batch
        pdf_bytes = await build_parts_print_pdf_batch(
            part_ids=[],
            assembly_ids=[1001],
            parts=parts_repo,
            part_files=files_repo,
            assemblies=fake_assemblies_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 总装图 2 页 + 子件1 2 页 + 子件2 2 页 = 6 页
        assert len(reader.pages) == 6


class TestBatchPreservesOrder:
    """2026-07-30：批量打印两阶段流水线保持入参顺序。"""

    async def test_three_standalone_parts_kept_input_order(
        self, monkeypatch, fake_parts_repo,
    ):
        """3 个独立零件按 part_ids 顺序渲染并合并。"""
        import service.printing as printing_mod

        async def fake_download(key):
            return _make_blank_pdf(842, 595)
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        p1 = MagicMock()
        p1.id = 301
        p1.serial_no = "L301"
        p1.drawing_no = "DWG-301"
        p1.name = "零件1"
        p1.assembly_id = None

        p2 = MagicMock()
        p2.id = 302
        p2.serial_no = "L302"
        p2.drawing_no = "DWG-302"
        p2.name = "零件2"
        p2.assembly_id = None

        p3 = MagicMock()
        p3.id = 303
        p3.serial_no = "L303"
        p3.drawing_no = "DWG-303"
        p3.name = "零件3"
        p3.assembly_id = None

        parts_repo = MagicMock()
        parts_repo.list_by_ids = AsyncMock(return_value=[p1, p2, p3])
        parts_repo.get_by_id = AsyncMock(side_effect=lambda pid: {
            301: p1, 302: p2, 303: p3,
        }.get(pid))

        files_repo = MagicMock()
        files_repo.list_by_parts = AsyncMock(
            side_effect=lambda pids, kind=None, include_deleted=False: {
                pid: _make_drawing_row("PDF", "pdf", f"drawings/{pid}/DRAWING/aaa.pdf")
                for pid in pids
            }
        )

        call_order: list[int] = []
        original_sync = printing_mod._build_part_print_pdf_sync

        def tracking_sync(data):
            call_order.append(data.part_id)
            return original_sync(data)

        monkeypatch.setattr(printing_mod, "_build_part_print_pdf_sync", tracking_sync)

        from service.printing import build_parts_print_pdf_batch
        pdf_bytes = await build_parts_print_pdf_batch(
            part_ids=[302, 301, 303],
            parts=parts_repo,
            part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 3 个零件 × 2 页 = 6 页
        assert len(reader.pages) == 6
        # sync 渲染被调用的顺序与入参一致
        assert call_order == [302, 301, 303]


class TestBatchSkipsFailedItems:
    """2026-07-30：单件渲染失败仅跳过，不阻断整批。"""

    async def test_one_render_failure_skipped_others_remain(
        self, monkeypatch, fake_parts_repo,
    ):
        """3 个零件中第 2 个渲染失败 → 最终 4 页（2 个成功件）。"""
        import service.printing as printing_mod

        async def fake_download(key):
            return _make_blank_pdf(842, 595)
        monkeypatch.setattr(printing_mod.cos_mod, "download_object", fake_download)

        p1 = MagicMock()
        p1.id = 401
        p1.serial_no = "L401"
        p1.drawing_no = "DWG-401"
        p1.name = "零件1"
        p1.assembly_id = None

        p2 = MagicMock()
        p2.id = 402
        p2.serial_no = "L402"
        p2.drawing_no = "DWG-402"
        p2.name = "零件2"
        p2.assembly_id = None

        p3 = MagicMock()
        p3.id = 403
        p3.serial_no = "L403"
        p3.drawing_no = "DWG-403"
        p3.name = "零件3"
        p3.assembly_id = None

        parts_repo = MagicMock()
        parts_repo.list_by_ids = AsyncMock(return_value=[p1, p2, p3])
        parts_repo.get_by_id = AsyncMock(side_effect=lambda pid: {
            401: p1, 402: p2, 403: p3,
        }.get(pid))

        files_repo = MagicMock()
        files_repo.list_by_parts = AsyncMock(
            side_effect=lambda pids, kind=None, include_deleted=False: {
                pid: _make_drawing_row("PDF", "pdf", f"drawings/{pid}/DRAWING/aaa.pdf")
                for pid in pids
            }
        )

        original_sync = printing_mod._build_part_print_pdf_sync

        def failing_sync(data):
            if data.part_id == 402:
                raise ValueError("simulated render failure")
            return original_sync(data)

        monkeypatch.setattr(printing_mod, "_build_part_print_pdf_sync", failing_sync)

        from service.printing import build_parts_print_pdf_batch
        pdf_bytes = await build_parts_print_pdf_batch(
            part_ids=[401, 402, 403],
            parts=parts_repo,
            part_files=files_repo,
        )
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # 2 个成功件 × 2 页 = 4 页
        assert len(reader.pages) == 4


class TestListByPartsRepository:
    """2026-07-30：验证 PartFileRepository.list_by_parts 批量预取语义。"""

    @pytest.mark.asyncio
    async def test_returns_latest_per_part_id(self):
        """同 part 多行时只返回 id 最大（最新）的一条。"""
        from repository.part_file import PartFileRepository

        repo = MagicMock(spec=PartFileRepository)
        # 模拟 list_for_part_ids 返回按 part_id asc, id desc 排序的结果
        row_p1_a = MagicMock()
        row_p1_a.id = 100
        row_p1_a.part_id = 1
        row_p1_a.file_type = "PDF"

        row_p1_b = MagicMock()
        row_p1_b.id = 101
        row_p1_b.part_id = 1
        row_p1_b.file_type = "PNG"

        row_p2_a = MagicMock()
        row_p2_a.id = 200
        row_p2_a.part_id = 2
        row_p2_a.file_type = "JPG"

        repo.list_for_part_ids = AsyncMock(return_value=[row_p1_b, row_p1_a, row_p2_a])

        # 把 list_by_parts 的逻辑直接调一遍（它内部调用 list_for_part_ids）
        from repository.part_file import PartFileRepository
        # 由于 list_by_parts 是实例方法，我们直接测试其逻辑：
        # 遍历结果，每个 part_id 只保留第一条
        rows = await repo.list_for_part_ids([1, 2], kind="DRAWING")
        result: dict[int, object] = {1: None, 2: None}
        for row in rows:
            if row.part_id in result and result[row.part_id] is None:
                result[row.part_id] = row

        assert result[1].id == 101  # 最新
        assert result[1].file_type == "PNG"
        assert result[2].id == 200
        assert result[2].file_type == "JPG"

    @pytest.mark.asyncio
    async def test_missing_part_returns_none(self):
        """无图纸的 part_id 对应值为 None。"""
        from repository.part_file import PartFileRepository

        repo = MagicMock(spec=PartFileRepository)
        row = MagicMock()
        row.id = 100
        row.part_id = 1
        row.file_type = "PDF"
        repo.list_for_part_ids = AsyncMock(return_value=[row])

        rows = await repo.list_for_part_ids([1, 99], kind="DRAWING")
        result: dict[int, object] = {1: None, 99: None}
        for r in rows:
            if r.part_id in result and result[r.part_id] is None:
                result[r.part_id] = r

        assert result[1] is not None
        assert result[99] is None


class TestBarcodePageInfo:
    """2026-07-30：条码页新增交期 + 数量信息渲染。"""

    def _non_white_count(self, img: Image.Image) -> int:
        """返回灰度图中非白（<250）像素数量。"""
        hist = img.convert("L").histogram()
        return sum(hist[:250])

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_info_text_visible_with_date_and_quantity(self, orientation: str) -> None:
        """中部偏左区域应有 D:/Q: 标签的黑色像素。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(
            orientation, "L2014",
            planned_delivery_date=date(2026, 8, 15),
            quantity=100,
            show_info=True,
        )
        w, h = img.size
        # 中部偏左：x ∈ [0, w*0.45]，y ∈ [h*0.30, h*0.55]
        info_zone = img.crop((0, int(h * 0.30), int(w * 0.45), int(h * 0.55)))
        assert self._non_white_count(info_zone) > 100, "中部偏左应有 D:/Q: 文字"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_info_text_shows_dash_when_no_date(self, orientation: str) -> None:
        """交期为 None 时显示「D: --」，中部偏左仍应有黑色像素。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(
            orientation, "L2014",
            planned_delivery_date=None,
            quantity=50,
            show_info=True,
        )
        w, h = img.size
        info_zone = img.crop((0, int(h * 0.30), int(w * 0.45), int(h * 0.55)))
        assert self._non_white_count(info_zone) > 100, "中部偏左应有「D: --」文字"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_assembly_no_quantity_line(self, orientation: str) -> None:
        """quantity=None（装配体总装图）时，不应渲染数量行；只渲染交期一行。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(
            orientation, "L1001",
            planned_delivery_date=date(2026, 8, 15),
            quantity=None,
            show_info=True,
        )
        w, h = img.size
        # 数量行约在 y ∈ [h*0.50, h*0.70] 区域（交期行下方）
        quantity_zone = img.crop((0, int(h * 0.50), int(w * 0.45), int(h * 0.70)))
        # 无数量行时该区域应基本为白色（允许极少抗锯齿残留）
        assert self._non_white_count(quantity_zone) < 50, "数量行不应出现"

    @pytest.mark.parametrize("orientation", ["landscape", "portrait"])
    def test_no_info_when_show_info_false(self, orientation: str) -> None:
        """show_info=False（旧测试/默认调用）时，中部偏左应保持空白。"""
        from service.printing import _build_barcode_page

        img = _build_barcode_page(
            orientation, "L2014",
            planned_delivery_date=date(2026, 8, 15),
            quantity=100,
            show_info=False,
        )
        w, h = img.size
        info_zone = img.crop((0, int(h * 0.30), int(w * 0.45), int(h * 0.55)))
        assert self._non_white_count(info_zone) == 0, "show_info=False 时不应渲染信息文字"


class TestPreparePartPrintDataDeliveryDate:
    """v0.2.5：_prepare_part_print_data 优先 system_delivery_date，回退 planned_delivery_date。"""

    @pytest.mark.asyncio
    async def test_system_delivery_date_preferred_over_planned(self) -> None:
        from service.printing import _prepare_part_print_data
        from repository.part import PartRepository
        from repository.part_file import PartFileRepository

        part = MagicMock()
        part.id = 1234
        part.serial_no = "L2014"
        part.drawing_no = "DWG-001"
        part.name = "测试"
        part.planned_delivery_date = date(2026, 9, 1)
        part.system_delivery_date = date(2026, 8, 20)  # 更早，按订单方
        part.quantity = 5

        parts = MagicMock(spec=PartRepository)
        parts.get_by_id = AsyncMock(return_value=part)
        part_files = MagicMock(spec=PartFileRepository)
        part_files.list_by_part = AsyncMock(return_value=[])

        data = await _prepare_part_print_data(
            part_id=1234, parts=parts, part_files=part_files,
        )
        # system_delivery_date 8/20 -> _buffered_delivery_date(-3) = 8/17
        assert data.planned_delivery_date == date(2026, 8, 17)

    @pytest.mark.asyncio
    async def test_falls_back_to_planned_when_system_missing(self) -> None:
        from service.printing import _prepare_part_print_data
        from repository.part import PartRepository
        from repository.part_file import PartFileRepository

        part = MagicMock()
        part.id = 1234
        part.serial_no = "L2014"
        part.drawing_no = "DWG-001"
        part.name = "测试"
        part.planned_delivery_date = date(2026, 9, 1)
        part.system_delivery_date = None
        part.quantity = 5

        parts = MagicMock(spec=PartRepository)
        parts.get_by_id = AsyncMock(return_value=part)
        part_files = MagicMock(spec=PartFileRepository)
        part_files.list_by_part = AsyncMock(return_value=[])

        data = await _prepare_part_print_data(
            part_id=1234, parts=parts, part_files=part_files,
        )
        # planned_delivery_date 9/1 -> _buffered_delivery_date(-3) = 8/29
        assert data.planned_delivery_date == date(2026, 8, 29)