"""
按 docs/26洪升宏在职人员统计表.xlsx 批量生成员工胸牌 PDF（A4 横向，4×5 网格）。

每张胸牌 70mm × 40mm 横向：
- 正中：姓名（粗体大字，水平 + 垂直居中）
- 底部：手机号 Code128 条形码（高度 = 20% 胸牌高度 = 8mm，距底边 2mm）
- 灰色虚线外框便于裁切

输出：tmp/employee_badges.pdf
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import openpyxl
from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont

# ── 路径 ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXCEL_PATH = PROJECT_ROOT / "docs" / "26洪升宏在职人员统计表.xlsx"
OUTPUT_PATH = PROJECT_ROOT / "tmp" / "employee_badges.pdf"

# ── A4 + 胸牌几何（mm） ─────────────────────────────
A4_LANDSCAPE_MM = (297, 210)
BADGE_W_MM, BADGE_H_MM = 70, 40
COLS, ROWS = 4, 5
GAP_MM = 1
PAGE_W_MM = COLS * BADGE_W_MM + (COLS - 1) * GAP_MM
PAGE_H_MM = ROWS * BADGE_H_MM + (ROWS - 1) * GAP_MM
MARGIN_X_MM = (A4_LANDSCAPE_MM[0] - PAGE_W_MM) / 2
MARGIN_Y_MM = (A4_LANDSCAPE_MM[1] - PAGE_H_MM) / 2

# ── 渲染分辨率 ──────────────────────────────────────
DPI = 300
MM_PER_INCH = 25.4
PX_PER_MM = DPI / MM_PER_INCH

PAGE_W_PX = int(A4_LANDSCAPE_MM[0] * PX_PER_MM)
PAGE_H_PX = int(A4_LANDSCAPE_MM[1] * PX_PER_MM)
BADGE_W_PX = int(BADGE_W_MM * PX_PER_MM)
BADGE_H_PX = int(BADGE_H_MM * PX_PER_MM)
GAP_X_PX = int(GAP_MM * PX_PER_MM)
GAP_Y_PX = int(GAP_MM * PX_PER_MM)
MARGIN_X_PX = int(MARGIN_X_MM * PX_PER_MM)
MARGIN_Y_PX = int(MARGIN_Y_MM * PX_PER_MM)


# ── 数据加载 ─────────────────────────────────────────
def load_employees(excel_path: Path) -> list[tuple[str, str]]:
    """从 Excel 读取 (姓名, 电话str)，跳过空行 + 清洗括号后缀。"""
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active
    employees: list[tuple[str, str]] = []
    paren_pat = re.compile(r"[（(].*?[)）]")
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row or row[0] is None:
            continue
        name = row[1]
        phone_int = row[3]
        if not name or phone_int is None:
            continue
        name_clean = paren_pat.sub("", str(name)).strip()
        phone_str = str(int(phone_int))
        employees.append((name_clean, phone_str))
    return employees


# ── Code128 条形码渲染 ───────────────────────────────
def render_barcode(data: str, module_height_mm: float = 12.0) -> Image.Image:
    """生成 Code128 条形码 PIL Image（白底黑条，不写文字标签）。

    module_height_mm: 条形码「条」的物理高度（不含 quiet zone）。
    """
    writer = ImageWriter()
    obj = Code128(data, writer=writer)
    opts = {
        "module_width": 0.3,
        "module_height": module_height_mm,
        "font_size": 0,
        "text_distance": 0,
        "quiet_zone": 2.0,
        "background": "white",
        "foreground": "black",
        "write_text": False,
    }
    buf = io.BytesIO()
    obj.write(buf, opts)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


# ── 中文字体加载 ─────────────────────────────────────
def load_cn_font(size: int) -> ImageFont.ImageFont:
    """尽量加载中文字体；找不到时 fallback 到默认（标签仍可显示）。"""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size=size)
    return ImageFont.load_default()


# ── 主流程 ───────────────────────────────────────────
def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    employees = load_employees(EXCEL_PATH)
    if not employees:
        raise SystemExit(f"未从 {EXCEL_PATH} 读到员工数据")

    page = Image.new("RGB", (PAGE_W_PX, PAGE_H_PX), "white")
    draw = ImageDraw.Draw(page)

    # 姓名字号：18pt @ 300dpi ≈ 75px（72dpi 基准下 18pt = 24px，× DPI/72）
    name_font = load_cn_font(size=int(18 * DPI / 72))

    # 排版：
    # - 姓名：垂直居中于胸牌正中（y_center = 胸牌高度 / 2 = 20mm）
    # - 条形码：贴底部，距底边 2mm（y_top = 胸牌高度 - 8 - 2 = 30mm）
    BARCODE_HEIGHT_MM = BADGE_H_MM * 0.20       # 8mm = 胸牌高度的 20%
    BARCODE_BOTTOM_MARGIN_MM = 2                # 条形码距底边 2mm
    BARCODE_TOP_MM = BADGE_H_MM - BARCODE_HEIGHT_MM - BARCODE_BOTTOM_MARGIN_MM  # 30mm

    BARCODE_HEIGHT_PX = int(BARCODE_HEIGHT_MM * PX_PER_MM)
    BARCODE_TOP_PX = int(BARCODE_TOP_MM * PX_PER_MM)

    for idx, (name, phone) in enumerate(employees):
        if idx >= COLS * ROWS:
            print(f"[warn] 胸牌槽位不足，第 {idx + 1} 人及之后跳过：{name}")
            break
        col, row = idx % COLS, idx // COLS
        x0 = MARGIN_X_PX + col * (BADGE_W_PX + GAP_X_PX)
        y0 = MARGIN_Y_PX + row * (BADGE_H_PX + GAP_Y_PX)
        x1, y1 = x0 + BADGE_W_PX, y0 + BADGE_H_PX

        # 1) 灰色裁切边框
        draw.rectangle([x0, y0, x1, y1], outline=(180, 180, 180), width=2)

        # 2) 姓名（胸牌正中央，水平 + 垂直居中）
        bbox = draw.textbbox((0, 0), name, font=name_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = x0 + (BADGE_W_PX - tw) // 2
        ty = y0 + (BADGE_H_PX - th) // 2 - int(1 * PX_PER_MM)  # 微调视觉重心
        draw.text((tx, ty), name, fill="black", font=name_font)

        # 3) 条形码（高度 8mm = 20%，贴底部 2mm 边距，水平居中）
        bc = render_barcode(phone, module_height_mm=BARCODE_HEIGHT_MM - 1.0)
        ratio = BARCODE_HEIGHT_PX / bc.height
        bc_w = int(bc.width * ratio)
        bc_h = int(bc.height * ratio)
        bc_resized = bc.resize((bc_w, bc_h), Image.LANCZOS)
        bc_x = x0 + (BADGE_W_PX - bc_w) // 2
        bc_y = y0 + BARCODE_TOP_PX
        page.paste(bc_resized, (bc_x, bc_y))

    page.save(OUTPUT_PATH, "PDF", resolution=DPI)
    print(f"✅ 已生成: {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size / 1024:.1f} KB)")
    print(f"   员工数: {len(employees)}（按 {COLS}×{ROWS} 网格排列）")


if __name__ == "__main__":
    main()