"""零件打印 PDF 合成 service。

为「订单详情 → 图纸/文件 → 打印」按钮生成的双面打印 PDF：
- 第 1 页 = 图纸（正面：上传的那一张；若无图纸则渲染一张信息卡占位）
- 第 2 页 = 条形码，反面固定角落位置（默认右下角），编码 `serial_no`

方向处理：自动检测上传 PDF / 图片的宽高比，图纸页是横向 A4 则条码页
也用横向 A4（保持打印机双面翻转方向一致）。无图纸时按零件原始朝向信息
默认横向（图纸常用横向）；最终落 A4（595×842 pt 或 842×595 pt）。

依赖（**全部跨平台**，部署在 Linux Docker 无任何影响）：
- `pillow`：图片/PDF 渲染
- `python-barcode`：Code128 条形码生成
- `pypdf`：合并 PDF 页面（纯 Python，无 poppler / 无 sips 等系统工具依赖）
"""
from __future__ import annotations

import asyncio
import io
import logging

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

from core import cos as cos_mod
from core.error_code import ErrCode
from core.exception import BizError
from model import TDrawingFile
from repository.drawing_file import DrawingFileRepository
from repository.part import PartRepository
from fastapi import status as http_status

_logger = logging.getLogger(__name__)

# A4 尺寸（pt）：portrait 595×842，landscape 842×595
A4_PORTRAIT = (595, 842)
A4_LANDSCAPE = (842, 595)

# 渲染像素尺寸（@ 150 dpi）
DPI = 150
PX_PER_PT = DPI / 72.0


def _a4_px(orientation: str) -> tuple[int, int]:
    """按方向返回 A4 像素尺寸 (w, h)。orientation ∈ {'portrait','landscape'}。"""
    w_pt, h_pt = A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
    return (int(w_pt * PX_PER_PT), int(h_pt * PX_PER_PT))


# ============================================================
# 条形码渲染
# ============================================================
def _render_barcode_pil(data: str) -> Image.Image:
    """生成 Code128 条形码 PIL Image（白底黑条，不写文字标签）。"""
    writer = ImageWriter()
    barcode_obj = Code128(data, writer=writer)
    opts = {
        "module_width": 0.3,
        "module_height": 12.0,
        "font_size": 0,
        "text_distance": 0,
        "quiet_zone": 3.0,
        "background": "white",
        "foreground": "black",
        "write_text": False,
    }
    buf = io.BytesIO()
    barcode_obj.write(buf, opts)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _load_cn_font(size: int) -> ImageFont.ImageFont:
    """尽量加载中文字体；找不到时 fallback 到默认（标签仍可显示）。"""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _build_barcode_page(orientation: str, serial_no: str) -> Image.Image:
    """渲染第 2 页（反面）：白底 A4 + 右下角条形码 + 醒目序列号。

    序列号同时：
    - 在条码下方作为扫描标签（小号）
    - 在条码上方以醒目大字号 + 灰色底色块显示（方便人工目视对单）
    """
    page_w, page_h = _a4_px(orientation)
    page = Image.new("RGB", (page_w, page_h), "white")
    draw = ImageDraw.Draw(page)

    # 字号随朝向微调：横向时页面更宽，字号更大
    big_label_size = 80 if orientation == "landscape" else 64
    big_value_size = 120 if orientation == "landscape" else 96
    small_tag_size = 36

    big_label_font = _load_cn_font(size=big_label_size)
    big_value_font = _load_cn_font(size=big_value_size)
    small_tag_font = _load_cn_font(size=small_tag_size)

    # === 右下角固定角落 ===
    # 条形码宽度按页面短边自适应：横向短边=595pt→~900px；纵向短边=595pt→~700px
    short_side_pt = min(*A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT)
    barcode_w_px = int(short_side_pt * PX_PER_PT * 0.55)  # 横 ~760px / 纵 ~570px
    barcode_w_px = max(barcode_w_px, 400)

    bc_img = _render_barcode_pil(serial_no)
    ratio = barcode_w_px / bc_img.width
    bc_resized = bc_img.resize(
        (barcode_w_px, int(bc_img.height * ratio)), Image.LANCZOS,
    )

    corner_margin_r_px = int(60 * PX_PER_PT)
    corner_margin_b_px = int(60 * PX_PER_PT)
    paste_x = page_w - corner_margin_r_px - barcode_w_px
    paste_y = page_h - corner_margin_b_px - bc_resized.height

    # 估算整个右下角块总高度（醒目序列号 + 条码 + 小标签 + 间距）
    big_label_gap = 28
    bc_to_tag_gap = 14
    tag_gap_to_block = 22
    # 倒推 paste_y 让整块底部对齐
    block_total_h = (
        big_label_size + big_label_gap
        + big_value_size + tag_gap_to_block
        + bc_resized.height + bc_to_tag_gap
        + small_tag_size
    )
    block_top_y = page_h - corner_margin_b_px - block_total_h
    # 在整块里绘制
    cur_y = block_top_y
    # 1) 灰色标签「序列号 / Serial No.」
    label_text = "序列号"
    bbox = draw.textbbox((0, 0), label_text, font=big_label_font)
    lw = bbox[2] - bbox[0]
    draw.rectangle(
        [paste_x, cur_y, paste_x + lw + 32, cur_y + big_label_size + 8],
        fill="#f0f0f0",
    )
    draw.text(
        (paste_x + 16, cur_y + 4), label_text, fill="#333", font=big_label_font,
    )
    cur_y += big_label_size + big_label_gap
    # 2) 醒目大字号序列号
    bbox = draw.textbbox((0, 0), serial_no, font=big_value_font)
    vw = bbox[2] - bbox[0]
    vh = bbox[3] - bbox[1]
    # 浅灰底色块 + 黑字
    draw.rectangle(
        [
            paste_x,
            cur_y - 6,
            paste_x + max(vw + 32, barcode_w_px),
            cur_y + vh + 12,
        ],
        fill="#fffbe6",
        outline="#e6a23c",
        width=3,
    )
    draw.text(
        (paste_x + 16, cur_y), serial_no, fill="#000", font=big_value_font,
    )
    cur_y += vh + tag_gap_to_block
    # 3) 条形码本体
    page.paste(bc_resized, (paste_x, cur_y))
    cur_y += bc_resized.height + bc_to_tag_gap
    # 4) 扫描标签（条码下方）
    bbox = draw.textbbox((0, 0), serial_no, font=small_tag_font)
    tw = bbox[2] - bbox[0]
    draw.text(
        (paste_x + (barcode_w_px - tw) // 2, cur_y),
        serial_no,
        fill="#000",
        font=small_tag_font,
    )

    return page


# ============================================================
# 信息卡占位（第 1 页，未上传图纸 / 不支持的文件类型）
# ============================================================
def _build_info_card_page(
    *,
    orientation: str,
    drawing_no: str,
    name: str,
    serial_no: str | None,
    customer_path: str | None,
) -> Image.Image:
    """渲染第 1 页占位。横向时字段按两栏排版，纵向时单栏。"""
    page_w, page_h = _a4_px(orientation)
    page = Image.new("RGB", (page_w, page_h), "white")
    draw = ImageDraw.Draw(page)

    title_font = _load_cn_font(size=56 if orientation == "landscape" else 44)
    body_font = _load_cn_font(size=42 if orientation == "landscape" else 32)
    small_font = _load_cn_font(size=26 if orientation == "landscape" else 22)

    title = "零件信息卡"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    draw.text(((page_w - tw) // 2, 120), title, fill="black", font=title_font)

    lines: list[tuple[str, str]] = [
        ("图号", drawing_no or "—"),
        ("名称", name or "—"),
        ("客户", customer_path or "—"),
        ("流水号", serial_no or "—"),
    ]
    y = 260
    x_label = 140
    x_value = 280
    for label, value in lines:
        draw.text((x_label, y), f"{label}:", fill="#444", font=body_font)
        draw.text((x_value, y), value, fill="black", font=body_font)
        y += body_font.size + 28

    note = ("提示：本零件尚未上传图纸，此页面为信息卡占位。"
            "如需打印图纸正文，请先在「图纸/文件」中上传 PDF/PNG/JPG 图纸。")
    max_chars_per_line = (page_w - 240) // small_font.size
    chunks = [note[i : i + max_chars_per_line] for i in range(0, len(note), max_chars_per_line)]
    ny = page_h - 180
    for chunk in chunks:
        bbox = draw.textbbox((0, 0), chunk, font=small_font)
        cw = bbox[2] - bbox[0]
        draw.text(((page_w - cw) // 2, ny), chunk, fill="#888", font=small_font)
        ny += small_font.size + 8

    return page


def _image_to_a4_pdf_bytes(img: Image.Image) -> bytes:
    """把 pillow Image 渲染成单页 PDF（bytes）。"""
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=DPI)
    return buf.getvalue()


def _detect_pdf_orientation(pdf_bytes: bytes) -> str:
    """读 PDF 第一页 mediabox 判断朝向：'landscape' or 'portrait'。"""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not reader.pages:
            return "landscape"
        box = reader.pages[0].mediabox
        return "landscape" if float(box.width) > float(box.height) else "portrait"
    except Exception:  # noqa: BLE001
        _logger.exception("failed to detect pdf orientation, default to landscape")
        return "landscape"


def _detect_image_orientation(img: Image.Image) -> str:
    return "landscape" if img.width > img.height else "portrait"


async def _download_drawing_bytes(drawing: TDrawingFile) -> bytes:
    """从 COS 拉图纸原始字节。"""
    return await cos_mod.download_object(drawing.object_key)


# ============================================================
# 主入口
# ============================================================
async def build_part_print_pdf(
    *,
    part_id: int,
    parts: PartRepository,
    drawings: DrawingFileRepository,
) -> bytes:
    """为指定零件生成「图纸 + 条形码」双面打印 PDF。

    朝向：自动跟随上传图纸的 orientation（landscape 优先，因图纸常用横向）；
    无图纸时默认 landscape。条码页 / 信息卡页与图纸页朝向一致，保证双面
    打印翻转方向正确。
    """
    part = await parts.get_by_id(part_id)
    if part is None:
        raise BizError(
            code=ErrCode.BIZ_PART_NOT_FOUND,
            message=f"part {part_id} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )

    # 取 master 图纸：page_index IS NULL 优先；否则取任意最新一条
    drawings_for_part = await drawings.list_by_part(part_id)
    master: TDrawingFile | None = None
    for d in drawings_for_part:
        if d.page_index is None and d.deleted_at is None:
            master = d
            break
    if master is None:
        for d in drawings_for_part:
            if d.deleted_at is None:
                master = d
                break

    serial_no = part.serial_no or "NO-SERIAL"

    # ---- 准备正面（图纸）----
    front_pdf_bytes: bytes | None = None
    orientation: str = "landscape"  # 默认横向（图纸常用）
    if master is not None:
        ext = master.file_type.upper()
        try:
            if ext == "PDF":
                front_pdf_bytes = await _download_drawing_bytes(master)
                orientation = _detect_pdf_orientation(front_pdf_bytes)
            elif ext in {"PNG", "JPG", "JPEG"}:
                raw = await _download_drawing_bytes(master)
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                orientation = _detect_image_orientation(img)
                page_w_px, page_h_px = _a4_px(orientation)
                ratio = min(page_w_px / img.width, page_h_px / img.height)
                new_w = int(img.width * ratio)
                new_h = int(img.height * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                canvas = Image.new("RGB", (page_w_px, page_h_px), "white")
                canvas.paste(img, ((page_w_px - new_w) // 2, (page_h_px - new_h) // 2))
                front_pdf_bytes = _image_to_a4_pdf_bytes(canvas)
            else:
                # STEP / DWG / DXF：纸面不可直接渲染 → 信息卡占位
                front_pdf_bytes = None
        except Exception:  # noqa: BLE001
            _logger.exception("failed to load master drawing, fallback to info card")
            front_pdf_bytes = None

    # ---- 拼装 PDF ----
    writer = PdfWriter()

    # 正面：原 PDF 直接合并；否则信息卡占位
    if front_pdf_bytes is not None:
        try:
            front_reader = PdfReader(io.BytesIO(front_pdf_bytes))
            for page in front_reader.pages:
                writer.add_page(page)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to merge drawing pdf, fallback to info card")
            front_pdf_bytes = None

    if front_pdf_bytes is None:
        info_card = _build_info_card_page(
            orientation=orientation,
            drawing_no=part.drawing_no or "",
            name=part.name or "",
            serial_no=part.serial_no,
            customer_path=None,
        )
        info_pdf_bytes = _image_to_a4_pdf_bytes(info_card)
        info_reader = PdfReader(io.BytesIO(info_pdf_bytes))
        for page in info_reader.pages:
            writer.add_page(page)

    # 反面：条码页（朝向与正面一致）
    barcode_page_img = _build_barcode_page(orientation, serial_no)
    barcode_pdf_bytes = _image_to_a4_pdf_bytes(barcode_page_img)
    barcode_reader = PdfReader(io.BytesIO(barcode_pdf_bytes))
    for page in barcode_reader.pages:
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()