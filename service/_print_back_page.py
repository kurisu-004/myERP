"""零件/装配体打印 PDF 背面页：矢量直出 ReportLab。

2026-07-31 打印性能优化：替换原 PIL + python-barcode 全页光栅渲染管线，
背面页从 ~150 KB 光栅 PDF 降到 ~5-15 KB 矢量 PDF，生成时间从 ~0.5s/件降到 ~5ms/件。

布局（A4 landscape 842 × 595 pt；以 serial="F1016" 实测对齐旧 PIL 光栅版）：
- 主条码：右半区纵向贴右边距 28pt（旋转 90° CCW），墨迹长度 238pt × 条高 121.5pt，
  → page x∈[692.5, 814.0]、y∈[178.5, 416.5]；
- 主序列号：旋转 90° CCW 紧贴主条码左缘，右缘 x=670.5，纵向居中；font 105pt，
  超长序列号自动缩字号避免溢出；
- 左下小组：条码贴左下 (28, 28)，序列号 baseline y=81.8 在其上方；font 31.2pt；
- 左上小组：左下几何整体 180° 旋转贴顶 margin 28 → 条码在上、倒置序号在下；
- D:/Q: 信息：左半区居中逐行；D 在上（ink 顶距页顶 0.35×595=208.25pt）、Q 在下，
  行距 16pt；font 57.6pt。

2026-07-31 修正：按旧 PIL 光栅版实测几何 1:1 重写绘制部分（旋转映射 + y 方向 +
INFO_FONT/INFO_LINE_GAP + 上半 180° 组 + D/Q 顺序等原有多处错误）。

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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_logger = logging.getLogger(__name__)

# 页尺寸（pt）。与 service.printing.A4_LANDSCAPE 同值；因 service.printing 顶层
# 会 import 本模块，这里固化常量而不反向 import，避免循环依赖。
A4_LANDSCAPE: tuple[int, int] = (842, 595)


@functools.lru_cache(maxsize=1)
def _register_print_font() -> str:
    """注册并返回打印用 TTF 字体名。

    序列号 / D:Q: 信息都是 ASCII 字符，DejaVuSans 子集已足够（体积仅几 KB）。
    CJK 信息卡另走 `_load_cn_font`（PIL）路径，不在这里。
    """
    candidates = [
        # Debian/Ubuntu apt install fonts-dejavu 安装位置（runtime 路径）
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
MAIN_SERIAL_FONT_PT = 105.0        # 序列号字体（pt）
MAIN_BC_INK_LEN_PT = 238.0         # 主条码墨迹长度（旋转后沿页面纵向）
MAIN_BC_BAR_HEIGHT_PT = 121.5      # 主条码条高度（旋转后沿页面横向）
MAIN_RIGHT_MARGIN_PT = 28          # 主条码距页面右边距（pt）
MAIN_SERIAL_BC_GAP_PT = 22         # 序列号 ↔ 主条码 间距（旋转后横向）

# === 小条码（左下水平 + 左上 180°）===
SMALL_SERIAL_FONT_PT = 31.2        # 小序列号字体（pt）
SMALL_BC_INK_LEN_PT = 85.7         # 小条码墨迹长度
SMALL_BC_BAR_HEIGHT_PT = 43.8      # 小条码条高度
SMALL_LEFT_MARGIN_PT = 28          # 距页面左边距（pt）
SMALL_BOTTOM_MARGIN_PT = 28        # 距页面下边距（pt）
SMALL_TOP_MARGIN_PT = 28           # 距页面上边距（pt）
SMALL_SERIAL_BC_GAP_PT = 10        # 小序列号 ↔ 小条码 间距

# === D:/Q: 信息（中部偏左）===
INFO_FONT_PT = 57.6                # 大字信息（pt；旧版 120px @150DPI）
INFO_INK_H_PT = 48.5               # 旧版实测 "D: 08/10" ink 高；脱离字体度量
INFO_LEFT_MARGIN_PT = 28           # 距页面左边距（pt）
INFO_LINE_GAP_PT = 16              # 两行间距（pt）
INFO_TOP_FRACTION = 0.35           # 首行 ink 顶距页顶的比例

# === 条码反算 ===
MIN_BAR_MODULE_PT = 0.7            # 长序列号防条码过细（≈0.25mm）


def _code128(data: str, *, ink_len_pt: float, bar_height_pt: float) -> Code128:
    """按目标墨迹长度反算 barWidth。quiet=False：留白由外部 margin 保证。"""
    modules = Code128(data, barWidth=1.0, humanReadable=False, quiet=False).width
    bar_w = max(MIN_BAR_MODULE_PT, ink_len_pt / modules)
    return Code128(
        data,
        barHeight=bar_height_pt,
        barWidth=bar_w,
        humanReadable=False,
        quiet=False,
    )


def _build_back_page_pdf(
    serial_no: str,
    *,
    planned_delivery_date: date | None = None,
    quantity: int | None = None,
    show_info: bool = False,
) -> bytes:
    """渲染单页 A4 landscape **矢量 PDF**（白底黑字黑条）作为打印背面。

    几何以 serial="F1016" 实测对齐旧 PIL 光栅版（见模块 docstring）。
    """
    page_w, page_h = A4_LANDSCAPE  # (842, 595)
    font = _register_print_font()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4_LANDSCAPE)

    # --- 1. 主条码：旋转 90° CCW，右缘贴 margin，纵向居中 ---
    bc = _code128(
        serial_no,
        ink_len_pt=MAIN_BC_INK_LEN_PT,
        bar_height_pt=MAIN_BC_BAR_HEIGHT_PT,
    )
    c.saveState()
    c.translate(page_w - MAIN_RIGHT_MARGIN_PT, (page_h - bc.width) / 2)
    c.rotate(90)
    bc.drawOn(c, 0, 0)  # → page x[692.5,814] y[178.5,416.5]
    c.restoreState()

    # --- 2. 主序列号：旋转 90° CCW，右缘紧贴条码左缘，纵向居中 ---
    font_pt = MAIN_SERIAL_FONT_PT
    sw = c.stringWidth(serial_no, font, font_pt)
    avail = page_h - 2 * MAIN_RIGHT_MARGIN_PT  # 539；超长序列号缩字号
    if sw > avail:
        font_pt *= avail / sw
        sw = avail
    c.saveState()
    c.translate(
        page_w - MAIN_RIGHT_MARGIN_PT - MAIN_BC_BAR_HEIGHT_PT - MAIN_SERIAL_BC_GAP_PT,
        (page_h - sw) / 2,
    )
    c.rotate(90)
    c.setFont(font, font_pt)
    c.drawString(0, 0, serial_no)  # baseline 落在 page x=670.5，字形向左长出
    c.restoreState()

    # --- 3. 左下小组：条码贴底，序号在其上方 ---
    bcs = _code128(
        serial_no,
        ink_len_pt=SMALL_BC_INK_LEN_PT,
        bar_height_pt=SMALL_BC_BAR_HEIGHT_PT,
    )
    bcs.drawOn(c, SMALL_LEFT_MARGIN_PT, SMALL_BOTTOM_MARGIN_PT)
    c.setFont(font, SMALL_SERIAL_FONT_PT)
    c.drawString(
        SMALL_LEFT_MARGIN_PT,
        SMALL_BOTTOM_MARGIN_PT + SMALL_BC_BAR_HEIGHT_PT + SMALL_SERIAL_BC_GAP_PT,
        serial_no,  # baseline y = 81.8
    )

    # --- 4. 左上小组：同一几何整体 180° 旋转，贴 top margin ---
    # 锚点取「组的右上角」：rotate(180) 下 page = (tx - lx, ty - ly)，
    # 局部按正立画（条码在下、序号在上）翻转后正好是条码在上、倒置序号在下。
    block_w = max(bcs.width, c.stringWidth(serial_no, font, SMALL_SERIAL_FONT_PT))
    c.saveState()
    c.translate(
        SMALL_LEFT_MARGIN_PT + block_w,
        page_h - SMALL_TOP_MARGIN_PT,  # 组的右上角
    )
    c.rotate(180)  # page = (tx - lx, ty - ly)
    bcs.drawOn(c, 0, 0)  # 局部底 → 页面顶：条码在上
    c.drawString(
        0,
        SMALL_BC_BAR_HEIGHT_PT + SMALL_SERIAL_BC_GAP_PT,
        serial_no,  # 倒置序号在下
    )
    c.restoreState()

    # --- 5. D:/Q: 信息，左半区逐行居中，D 在上 Q 在下 ---
    if show_info:
        if planned_delivery_date is not None:
            d_line = f"D: {planned_delivery_date.month:02d}/{planned_delivery_date.day:02d}"
        else:
            d_line = "D: --"
        lines = [d_line]
        if quantity is not None:
            lines.append(f"Q: {quantity}")

        c.setFont(font, INFO_FONT_PT)
        line_h = INFO_INK_H_PT + INFO_LINE_GAP_PT
        for i, line in enumerate(lines):
            ink_top = page_h - page_h * INFO_TOP_FRACTION - i * line_h  # y-up
            tw = c.stringWidth(line, font, INFO_FONT_PT)
            x = INFO_LEFT_MARGIN_PT + (page_w / 2 - INFO_LEFT_MARGIN_PT - tw) / 2
            c.drawString(x, ink_top - INFO_INK_H_PT, line)

    c.showPage()
    c.save()
    return buf.getvalue()