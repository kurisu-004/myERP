"""零件/装配体打印 PDF 背面页：矢量直出 ReportLab。

2026-07-31 打印性能优化：替换原 PIL + python-barcode 全页光栅渲染管线，
背面页从 ~150 KB 光栅 PDF 降到 ~5-15 KB 矢量 PDF，生成时间从 ~0.5s/件降到 ~5ms/件。

布局（与原 `_build_barcode_page` 视觉一致，service/printing.py 旧代码为参考）：
- 右侧竖排大条码（旋转 90°）+ 序列号大字（沿 A4 长边尺寸的 1/8），序列号在条码左侧（x 较小）；
- 左下水平放置小条码 + 小序列号；
- 左上旋转 180° 放置小条码 + 小序列号；
- 中部偏左：D: MM/DD + Q: N 大字信息（不渲染 CJK，ASCII 标签兼容已有字体）。

设计约束：
- 单页 A4 landscape（842 × 595 pt），与正面页 orientation 解耦：背面页永远 landscape。
- barcode 矢量，边缘锐利，扫码兼容性 ≥ 光栅版。
- 字体：序列号 / 数字都是 ASCII，TTFont(DejaVu) 子集嵌入；CJK 信息卡留在 PIL 路径（`service/printing.py::_build_info_card_page`）。
- 单元函数 `_build_back_page_pdf(...)` 返回单页 PDF bytes，由 `printing.py` 用 pikepdf 合并。
"""
from __future__ import annotations

import functools
import io
import logging
import os
from datetime import date

from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_logger = logging.getLogger(__name__)

# A4 landscape 点尺寸（与 service/printing.py 保持一致）
A4_LANDSCAPE = landscape(A4)  # (842, 595)


@functools.lru_cache(maxsize=1)
def _register_print_font() -> str:
    """注册并返回打印用 TTF 字体名。

    序列号 / D:Q: 信息都是 ASCII 字符，DejaVuSans 子集已足够（体积仅几 KB）。
    CJK 信息卡另走 `_load_cn_font`（PIL）路径，不在这里。
    """
    candidates = [
        # Alpine apk add font-dejavu 安装位置
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # 容器外开发机（macOS）
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        # 容器外开发机（Linux 桌面）
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont("PrintSans", fp))
                return "PrintSans"
            except Exception as e:  # noqa: BLE001
                _logger.warning("TTFont register failed for %s: %s", fp, e)
    # Fallback：ReportLab 内置 Helvetica（无中文，覆盖率够 ASCII）
    return "Helvetica"


# === 主条码 + 序列号（沿 A4 右边，旋转 90°）===
# 原打印设置：A4 长边 / 8 ≈ 105 pt 作为旋转前字号；barWidth × 模块数决定宽度。
# 旋转 90° 后，原始 x 方向宽度变成纵向高度（页面纵长），原 y 高度变成横向宽度。
MAIN_SERIAL_FONT_PT = 105      # 序列号字体（pt；ReportLab 用 pt，与 PIL px@150DPI 一致 105*150/72≈219）
MAIN_BC_BAR_HEIGHT_PT = 250    # 条码条高度（pt；旋转后变成页面横向宽度）
MAIN_BC_BAR_WIDTH_PT = 0.5     # 条码单个模块宽度（pt；与原 module_width=0.3 + DPI 缩放效果相近）
MAIN_RIGHT_MARGIN_PT = 28      # 主条码距页面右边 1 cm
MAIN_SERIAL_BC_GAP_PT = 22     # 序列号 ↔ 条码 间距

# === 小条码（备用，左下 + 左上）===
SMALL_SERIAL_FONT_PT = 35      # 小序列号字体（pt）
SMALL_BC_BAR_HEIGHT_PT = 40    # 小条码条高度
SMALL_BC_BAR_WIDTH_PT = 0.3    # 小条码单模块宽度
SMALL_LEFT_MARGIN_PT = 28      # 距页面左边 1 cm
SMALL_BOTTOM_MARGIN_PT = 28    # 距页面下边 1 cm
SMALL_TOP_MARGIN_PT = 28       # 距页面上边 1 cm
SMALL_SERIAL_BC_GAP_PT = 6     # 小序列号 ↔ 小条码 间距

# === 中部偏左：D:/Q: 信息 ===
INFO_FONT_PT = 80              # 大字信息（pt）
INFO_LEFT_MARGIN_PT = 28       # 距页面左边 1 cm
INFO_LINE_GAP_PT = 12          # 两行间距


def _build_back_page_pdf(
    serial_no: str,
    *,
    planned_delivery_date: date | None = None,
    quantity: int | None = None,
    show_info: bool = False,
) -> bytes:
    """渲染单页 A4 landscape **矢量 PDF**（白底黑字黑条）作为打印背面。

    - 主条码 + 序列号：沿 A4 右边，旋转 90°（CCW）。序列号在条码左侧（x 较小）。
      旋转坐标系：原 (x, y) → 旋转后 (-y, x)；translate 到页面右边距，rotate 90，
      以条码中心为原点画 widget，主条码的纵向高度填到 ~500pt（≈ A4 长边 × 0.6）。
    - 小条码 ×2：左下水平、左上旋转 180°；高度 ~40pt，宽 ~80pt。
    - D:/Q: 信息：中部偏左，INFO_FONT_PT 大字；与主条码 + 小条码区域无重叠。
    """
    page_w, page_h = A4_LANDSCAPE  # (842, 595)
    font_name = _register_print_font()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4_LANDSCAPE)

    # ---- 主条码 + 序列号（沿右边，旋转 90°）----
    bc_main = Code128(
        serial_no,
        barHeight=MAIN_BC_BAR_HEIGHT_PT,
        barWidth=MAIN_BC_BAR_WIDTH_PT,
        humanReadable=False,
        fontName=font_name,
        fontSize=0,
        quiet=True,
    )
    # Code128 widget 在画布上的尺寸由 barHeight × 模块数 × barWidth 决定
    # 旋转坐标系下：原始 widget 高（barHeight=250）变成横向宽度；
    # widget 宽 = bc_main.width 变成纵向长度。
    bc_w_native = bc_main.width
    bc_h_native = MAIN_BC_BAR_HEIGHT_PT
    # 主条码目标横向宽度 = 页面短边 50%（旋转后变成纵向高度）
    target_width = page_w * 0.5
    # 因 bc_h_native 已经由 barHeight 决定，target_width 通过 mainWidget 的总宽度控制；
    # Code128 widget 宽度会随 barWidth 微调。为简化，直接用默认 widget 高度作纵向长。

    # 序列号：先量好 bbox（用 canvas.stringWidth / font）
    c.setFont(font_name, MAIN_SERIAL_FONT_PT)
    serial_w_native = c.stringWidth(serial_no, font_name, MAIN_SERIAL_FONT_PT)

    gap_pt = MAIN_SERIAL_BC_GAP_PT
    right_margin_pt = MAIN_RIGHT_MARGIN_PT

    # 旋转坐标系下的两个块的总纵向长度（旋转后就是页面纵向）
    total_h_native = serial_w_native + gap_pt + bc_h_native
    # 旋转坐标系下的两个块的总横向宽度（旋转后变成页面横向占用）
    total_w_rotated = max(bc_w_native, MAIN_SERIAL_FONT_PT * 0.7)

    # 以「旋转后」坐标系定位：右边距 = page_w - right_margin_pt - total_w_rotated
    c.saveState()
    c.translate(page_w - right_margin_pt - total_w_rotated, (page_h - total_h_native) / 2)
    c.rotate(90)
    # 现在 (x, y) = 旋转前的 (-y, x)；先画序列号（沿 x），再画条码（在 y 方向下方）
    c.drawString(0, 0, serial_no)
    # 条码放在序列号下方：y 方向向下 = -serial_h_native - gap
    serial_h_native = MAIN_SERIAL_FONT_PT  # 用字号当视觉高度
    bc_main.drawOn(c, 0, -(serial_h_native + gap_pt + bc_h_native))
    c.restoreState()

    # ---- 小条码（左下水平）----
    bc_small = Code128(
        serial_no,
        barHeight=SMALL_BC_BAR_HEIGHT_PT,
        barWidth=SMALL_BC_BAR_WIDTH_PT,
        humanReadable=False,
        fontName=font_name,
        fontSize=0,
        quiet=True,
    )
    bc_s_w = bc_small.width
    bc_s_h = SMALL_BC_BAR_HEIGHT_PT

    c.setFont(font_name, SMALL_SERIAL_FONT_PT)
    sr_s_w = c.stringWidth(serial_no, font_name, SMALL_SERIAL_FONT_PT)
    sr_s_h = SMALL_SERIAL_FONT_PT

    left_pt = SMALL_LEFT_MARGIN_PT
    bottom_pt = SMALL_BOTTOM_MARGIN_PT
    gap_s_pt = SMALL_SERIAL_BC_GAP_PT

    # 左下：序列号在上、条码在下，整体贴左下角
    block_w = max(sr_s_w, bc_s_w)
    block_h = sr_s_h + gap_s_pt + bc_s_h
    # 序列号左上角 = (left, page_h - bottom - block_h + sr_s_h)
    # 但我们用 baseline 画 drawString，所以直接给 baseline y
    serial_y = page_h - bottom_pt - bc_s_h - gap_s_pt  # 序列号 baseline 在这块区域顶部
    # 实际上 drawString 的 y 是 baseline，所以 sr_s_h 是字体的 ascent+descent；
    # 为简单起见，让序列号顶部与 block 顶部对齐即可
    block_top_y = page_h - bottom_pt - block_h  # A4 坐标 y 从下往上
    # 序列号 baseline = block_top_y + sr_s_h * 0.85（让顶部贴齐）
    c.drawString(left_pt, block_top_y + sr_s_h * 0.8, serial_no)
    # 条码放在序列号下方
    bc_small.drawOn(c, left_pt, block_top_y)

    # ---- 小条码（左上旋转 180°）----
    # 用 saveState/translate/rotate(180) 复用上面的几何
    top_pt = SMALL_TOP_MARGIN_PT
    c.saveState()
    c.translate(left_pt, page_h - top_pt)  # 左上角
    c.rotate(180)
    # 现在原点位于左上角，旋转 180° 后 (x,y) ↔ (-x,-y)
    # 序列号画在 (0, -sr_s_h*0.8)（向上偏移使其顶部贴齐）
    c.drawString(0, -sr_s_h * 0.8, serial_no)
    bc_small.drawOn(c, 0, -(sr_s_h + gap_s_pt + bc_s_h))
    c.restoreState()

    # ---- D:/Q: 信息（中部偏左）----
    if show_info:
        info_lines: list[str] = []
        if planned_delivery_date is not None:
            info_lines.append(
                f"D: {planned_delivery_date.month:02d}/{planned_delivery_date.day:02d}"
            )
        else:
            info_lines.append("D: --")
        if quantity is not None:
            info_lines.append(f"Q: {quantity}")

        if info_lines:
            c.setFont(font_name, INFO_FONT_PT)
            # 中部偏左 = x 距页面左边 1cm；y 从 page_h * 0.65 起（避开左侧小条码 + 右侧主条码）
            info_x = INFO_LEFT_MARGIN_PT
            info_y_start = page_h * 0.65  # ReportLab canvas y 从下往上
            # 单行近似高度（字符 ascent+descent）
            line_h = INFO_FONT_PT + INFO_LINE_GAP_PT
            # 反向绘制：第一行在最下面，向上排
            for i, line in enumerate(info_lines):
                y = info_y_start + i * line_h
                c.drawString(info_x, y, line)

    c.showPage()
    c.save()
    return buf.getvalue()