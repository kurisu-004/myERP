"""
生成 Z001-Z010 条形码 A4 打印页（可打印 PDF）
"""

import os
from io import BytesIO

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont

# ── 配置 ──────────────────────────────────────────────
CODES = [f"Z{i:03d}" for i in range(1, 11)]  # Z001 ~ Z010
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "../tmp/barcode_sheet_a4.pdf")

# A4 @ 300 DPI
A4_W, A4_H = 2480, 3508  # pixels
MARGIN = 120              # 页边距
GAP_X, GAP_Y = 100, 80   # 横竖间距

# 每页列数 / 行数
COLS, ROWS = 2, 5

BARCODE_W = (A4_W - 2 * MARGIN - (COLS - 1) * GAP_X) // COLS
BARCODE_H = (A4_H - 2 * MARGIN - (ROWS - 1) * GAP_Y) // ROWS

# 条形码图片的宽高比（含下方文字留白）
CODE_W = int(BARCODE_W * 0.85)
CODE_H = int(CODE_W * 0.35)   # 条形码本身高度
LABEL_H = int(BARCODE_H * 0.25)  # 标签文字高度

print(f"A4 尺寸: {A4_W}x{A4_H}")
print(f"每格尺寸: {BARCODE_W}x{BARCODE_H}")
print(f"条形码尺寸: {CODE_W}x{CODE_H}")
print(f"标签高度: {LABEL_H}")


def render_barcode(data: str) -> Image.Image:
    """生成 Code128 条形码 PIL Image（白色背景、黑色条码）"""
    # 用 ImageWriter 生成图片，options 控制外观
    writer = ImageWriter()
    barcode_obj = Code128(data, writer=writer)
    opts = {
        "module_width": 0.25,
        "module_height": 10.0,
        "font_size": 0,          # 不要自动标签，我们自己画
        "text_distance": 0,
        "quiet_zone": 3.0,
        "background": "white",
        "foreground": "black",
        "write_text": False,
    }
    # 写入 BytesIO 再读回来
    buf = BytesIO()
    barcode_obj.write(buf, opts)
    buf.seek(0)
    img = Image.open(buf).convert("RGB")
    return img


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    # 创建 A4 白底画布
    page = Image.new("RGB", (A4_W, A4_H), "white")
    draw = ImageDraw.Draw(page)

    # 尝试加载中文字体（若无则 fallback 到默认）
    font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font = None
    for fp in font_paths:
        if os.path.exists(fp):
            font = ImageFont.truetype(fp, size=60)
            break
    if font is None:
        font = ImageFont.load_default()

    for idx, code in enumerate(CODES):
        col = idx % COLS
        row = idx // COLS

        # 当前格子的左上角起点
        x0 = MARGIN + col * (BARCODE_W + GAP_X)
        y0 = MARGIN + row * (BARCODE_H + GAP_Y)

        # 格子中心
        cx = x0 + BARCODE_W // 2
        cy = y0 + BARCODE_H // 2

        # ── 生成条形码图片 ──
        barcode_img = render_barcode(code)
        # 等比例缩放至 CODE_W
        ratio = CODE_W / barcode_img.width
        new_h = int(barcode_img.height * ratio)
        barcode_img_resized = barcode_img.resize((CODE_W, new_h), Image.LANCZOS)

        # 粘贴（居中，偏上留标签位）
        paste_x = cx - CODE_W // 2
        paste_y = cy - new_h // 2 - LABEL_H // 2
        page.paste(barcode_img_resized, (paste_x, paste_y))

        # ── 下方文字标签（Z001） ──
        label_y = paste_y + new_h + 10
        bbox = draw.textbbox((0, 0), code, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((cx - tw // 2, label_y), code, fill="black", font=font)

    # 保存为 PDF（Pillow 直接支持）
    page.save(OUTPUT_PATH, "PDF", resolution=300)
    print(f"\n✅ 已生成: {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
