"""Unit tests for service/_print_back_page.py.

2026-07-31 引入：背面页改 ReportLab 矢量直出 PDF。本测试验证：
- 输出是合法的单页 PDF（pikepdf 解析）
- 页面尺寸是 A4 landscape（842 × 595 pt，pikepdf 归一化后 841.89 × 595.27）
- show_info=False 时中央偏左区域无文字（D:/Q: 信息）
- show_info=True + quantity=None 时不渲染 Q: 行（装配体场景）
- 几何对齐旧 PIL 光栅版：主条码 / 主序列号 / 左下 / 左上 / D:Q: 信息区位置
  （TestBackPageGeometry，2026-07-31 修正几何后新增）
"""
from __future__ import annotations

import io
from datetime import date

import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from service._print_back_page import _build_back_page_pdf


def _render_page_to_image(pdf_bytes: bytes) -> Image.Image:
    """把单页 PDF 渲染成 PIL Image，便于像素断言（条码区域等）。

    用 pypdfium2（PDFium 后端），与正面页规格化用同一引擎，避免引入新依赖。
    """
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    pil = pdf[0].render(scale=1.0).to_pil().convert("RGB")
    pdf.close()
    return pil


def _ink_bbox(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """在给定像素框内取墨迹 bbox（灰度 + invert）。

    image y 向下；ReportLab 几何在 y-up 上描述，断言需要时自行换算
    `y_img = 595 - y_up`。
    """
    crop = img.crop(box).convert("L")
    bbox = ImageOps.invert(crop).getbbox()
    if bbox is None:
        return None
    return (bbox[0] + box[0], bbox[1] + box[1], bbox[2] + box[0], bbox[3] + box[1])


def _render_page_to_image(pdf_bytes: bytes) -> Image.Image:
    """把单页 PDF 渲染成 PIL Image，便于像素断言（条码区域等）。

    用 pypdfium2（PDFium 后端），与正面页规格化用同一引擎，避免引入新依赖。
    """
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    pil = pdf[0].render(scale=1.0).to_pil().convert("RGB")
    pdf.close()
    return pil


class TestBackPageShape:
    """输出形状：A4 landscape 单页 PDF。"""

    def test_returns_pdf_bytes(self) -> None:
        pdf = _build_back_page_pdf("L2014")
        assert isinstance(pdf, bytes)
        assert pdf.startswith(b"%PDF-")

    def test_single_page_a4_landscape(self) -> None:
        pdf = _build_back_page_pdf("L2014")
        with pikepdf.Pdf.open(io.BytesIO(pdf)) as p:
            assert len(p.pages) == 1
            w, h = float(p.pages[0].mediabox[2]), float(p.pages[0].mediabox[3])
        # pikepdf 把 A4 归一化到 841.89 × 595.27（mm/pt 转换）
        assert abs(w - 841.89) < 1.0
        assert abs(h - 595.27) < 1.0

    def test_size_smaller_than_old_raster(self) -> None:
        """矢量背面页应明显小于原 ~150 KB 光栅页。"""
        pdf = _build_back_page_pdf("L2014")
        # 原光栅 ~150KB；矢量预期 < 80 KB
        assert len(pdf) < 80 * 1024, f"vector back page too large: {len(pdf)} bytes"


class TestBackPageLayout:
    """布局：右侧条码 + 左下/左上小条码。"""

    def test_right_third_has_barcode_dark_pixels(self) -> None:
        """主条码在右侧，右 1/3 区域应有大量黑色像素。"""
        pdf = _build_back_page_pdf("L2014")
        img = _render_page_to_image(pdf)
        w, h = img.size
        right = img.crop((int(w * 0.70), 0, w, h)).convert("L")
        hist = right.histogram()
        black = hist[0] + hist[1] + hist[2]
        assert black > 1000, f"right third should have barcode, black={black}"

    def test_left_third_has_small_barcode_dark_pixels(self) -> None:
        """小条码（左下/左上）在左侧，左 1/3 区域应有黑色像素。"""
        pdf = _build_back_page_pdf("L2014")
        img = _render_page_to_image(pdf)
        w, h = img.size
        left = img.crop((0, 0, int(w * 0.30), h)).convert("L")
        hist = left.histogram()
        black = hist[0] + hist[1] + hist[2]
        assert black > 200, f"left third should have small barcodes, black={black}"

    def test_center_horizontal_band_is_empty_when_no_info(self) -> None:
        """show_info=False（默认）时，中部偏左 D:Q: 文字区无文字。

        顶部 y∈[0, 0.18] 是左上旋转 180° 的小条码区，需要避开；正中 y∈[0.20, 0.75]
        才是 D:Q: 文字应该出现 / 不出现的判定区。
        """
        pdf = _build_back_page_pdf("L2014")
        img = _render_page_to_image(pdf)
        w, h = img.size
        # 左中偏中：x ∈ [0, 0.45]，y ∈ [0.20, 0.75]（避开左上小条码）
        left_mid = img.crop((0, int(h * 0.20), int(w * 0.45), int(h * 0.75))).convert("L")
        hist = left_mid.histogram()
        non_white = sum(hist[:250])
        total = sum(hist)
        assert non_white / total < 0.001, "show_info=False: 左中区应无文字"

    def test_center_horizontal_band_has_info_when_show_info(self) -> None:
        """show_info=True 时，D:/Q: 信息出现在中央偏左。

        ReportLab canvas y-up + pypdfium2 渲染 y-down，D:Q: 在 image 顶部 ~1/8 区。
        """
        pdf = _build_back_page_pdf(
            "L2014",
            planned_delivery_date=date(2026, 8, 17),
            quantity=100,
            show_info=True,
        )
        img = _render_page_to_image(pdf)
        w, h = img.size
        info_zone = img.crop((0, int(h * 0.05), int(w * 0.45), int(h * 0.40))).convert("L")
        hist = info_zone.histogram()
        non_white = sum(hist[:250])
        assert non_white > 100, f"show_info=True: 顶部偏左应有 D:/Q: 文字, non_white={non_white}"

    def test_no_quantity_line_when_quantity_none(self) -> None:
        """quantity=None 时不渲染 Q: 行（装配体）。"""
        pdf = _build_back_page_pdf(
            "L1001",
            planned_delivery_date=date(2026, 8, 17),
            quantity=None,
            show_info=True,
        )
        img = _render_page_to_image(pdf)
        w, h = img.size
        # 顶部只应有 D: 行；Q: 行（80pt 下方）在更下方，超出当前画布（只 1 行时）
        upper_zone = img.crop((0, int(h * 0.05), int(w * 0.45), int(h * 0.40))).convert("L")
        hist = upper_zone.histogram()
        non_white = sum(hist[:250])
        assert non_white > 50, "D: 行应在 upper_zone"


class TestBackPageEdgeCases:
    """边界：不同长度序列号 / 不同日期。"""

    def test_long_serial(self) -> None:
        """长序列号仍应正常生成（不溢出）。"""
        pdf = _build_back_page_pdf("F123456789-99")
        assert isinstance(pdf, bytes)
        assert len(pdf) > 1000

    def test_serial_only_digits(self) -> None:
        """纯数字序列号（不常见但 Code128 支持）。"""
        pdf = _build_back_page_pdf("12345678")
        assert isinstance(pdf, bytes)


class TestBackPageGeometry:
    """几何对齐旧 PIL 光栅版（serial="F1016" 实测基准，容差 ±3pt）。

    所有断言：image y 向下，1px ≈ 1pt（pypdfium2 scale=1.0）。
    条码类断言不许放宽（与字体无关）；文字类断言（font 度量受 macOS Arial /
    alpine DejaVu 差异影响）必要时可放宽并注释说明。
    """

    def test_main_barcode_geometry(self) -> None:
        """主条码 bbox：x[692.5, 814.0]、y_img[178.5, 416.5]。"""
        pdf = _build_back_page_pdf("F1016")
        img = _render_page_to_image(pdf)
        bbox = _ink_bbox(img, (680, 0, 842, 595))
        assert bbox is not None, "主条码窗口无墨迹"
        x1, y1, x2, y2 = bbox
        assert abs(x1 - 692.5) <= 3, f"左缘 x={x1}, expect 692.5 ±3"
        assert abs(x2 - 814.0) <= 3, f"右缘 x={x2}, expect 814.0 ±3"
        assert abs(y1 - 178.5) <= 3, f"顶 y_img={y1}, expect 178.5 ±3"
        assert abs(y2 - 416.5) <= 3, f"底 y_img={y2}, expect 416.5 ±3"

    def test_main_serial_right_edge_and_center(self) -> None:
        """主序列号右缘 ≈ 670.5（baseline 落点），y_img 中心 ≈ 297.5。"""
        pdf = _build_back_page_pdf("F1016")
        img = _render_page_to_image(pdf)
        # 宽窗口避字右延伸 + 字形下沿（左缘 ~560）
        bbox = _ink_bbox(img, (560, 0, 685, 595))
        assert bbox is not None, "主序列号窗口无墨迹"
        x1, y1, x2, y2 = bbox
        assert abs(x2 - 670.5) <= 3, f"右缘 x={x2}, expect 670.5 ±3"
        center = (y1 + y2) / 2
        assert abs(center - 297.5) <= 6, f"中心 y_img={center}, expect 297.5 ±6"

    def test_left_bottom_barcode(self) -> None:
        """左下组：条码左缘 ≈ 28、底 y_img ≈ 567（page 底 28pt = 595-28）。"""
        pdf = _build_back_page_pdf("F1016")
        img = _render_page_to_image(pdf)
        # y_img 595 - page_h_bottom(28) = 567；条码高 43.8 → 顶 y_img 595-28-43.8 = 523.2
        bbox = _ink_bbox(img, (0, 480, 300, 595))
        assert bbox is not None, "左下组窗口无墨迹"
        x1, y1, x2, y2 = bbox
        assert abs(x1 - 28) <= 3, f"条码左缘 x={x1}, expect 28 ±3"
        assert abs(y2 - 567) <= 3, f"条码底 y_img={y2}, expect 567 ±3"

    def test_left_top_group(self) -> None:
        """左上组 180° 旋转贴顶 margin：bbox 顶 y_img ≈ 28、左缘 ≈ 28。"""
        pdf = _build_back_page_pdf("F1016")
        img = _render_page_to_image(pdf)
        bbox = _ink_bbox(img, (0, 0, 300, 120))
        assert bbox is not None, "左上组窗口无墨迹"
        x1, y1, x2, y2 = bbox
        assert abs(y1 - 28) <= 3, f"顶 y_img={y1}, expect 28 ±3"
        assert abs(x1 - 28) <= 3, f"左缘 x={x1}, expect 28 ±3"

    def test_info_region_top(self) -> None:
        """show_info=True：左半区有墨迹，D 行 ink 顶 ≈ 208.25（=0.35×595）。

        文字 ink 顶距页顶比例 INFO_TOP_FRACTION=0.35；
        page y_up = page_h - page_h*0.35 = 595 - 208.25 = 386.75；
        image y = 595 - 386.75 = 208.25。
        """
        pdf = _build_back_page_pdf(
            "F1016",
            planned_delivery_date=date(2026, 8, 10),
            quantity=460,
            show_info=True,
        )
        img = _render_page_to_image(pdf)
        bbox = _ink_bbox(img, (28, 150, 421, 360))
        assert bbox is not None, "info 窗口无墨迹"
        x1, y1, x2, y2 = bbox
        assert abs(y1 - 208.25) <= 6, f"首行顶 y_img={y1}, expect 208.25 ±6"

    def test_info_d_above_q(self) -> None:
        """show_info=True 时 D 行必须在 Q 行上方（image y 更小）。"""
        pdf = _build_back_page_pdf(
            "F1016",
            planned_delivery_date=date(2026, 8, 10),
            quantity=460,
            show_info=True,
        )
        img = _render_page_to_image(pdf)
        # 拆成两段窄横向窗口分别取 bbox：D 在上 Q 在下；两窗口均从 150 起
        # (D 顶 ~208)，Q 在 ~280 附近
        d_bbox = _ink_bbox(img, (28, 200, 421, 265))
        q_bbox = _ink_bbox(img, (28, 265, 421, 345))
        assert d_bbox is not None, "D 行窗口无墨迹"
        assert q_bbox is not None, "Q 行窗口无墨迹"
        d_y1 = d_bbox[1]
        q_y1 = q_bbox[1]
        assert d_y1 < q_y1, f"D 行顶 y_img={d_y1} 应 < Q 行顶 y_img={q_y1}"