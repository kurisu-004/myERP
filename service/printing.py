"""零件打印 PDF 合成 service。

为「订单详情 → 图纸/文件 → 打印」按钮生成的双面打印 PDF：
- 第 1 页 = 图纸（正面上传的那一张；若无图纸则渲染一张信息卡占位）
- 第 2 页 = 条形码，反面固定角落位置（默认右下角）

依赖：
- `pillow`：图片/PDF 渲染
- `python-barcode`：Code128 条形码生成
- `pypdf`：合并已有 PDF 与新生成的条形码页（无需 poppler）

条码编码内容：当前零件的 `serial_no`（如 `F2036`），与 `Barcode.vue` 组件一致。
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

# A4 @ 72 dpi（PDF 默认单位 pt）：595 x 842 pt
A4_W_PT, A4_H_PT = 595, 842
# A4 @ 150 dpi（pillow 渲染用）：1240 x 1754 px
A4_W_PX, A4_H_PX = 1240, 1754
# 条形码页上条码区尺寸（角落位置条形码 + 下方文字标签）
BARCODE_W = 380  # px
BARCODE_H = 110  # px（Code128 + 留白）
# 条码在第 2 页右下角的边距
CORNER_MARGIN_R = 60  # px
CORNER_MARGIN_B = 60  # px


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


def _build_barcode_page(serial_no: str) -> Image.Image:
    """渲染第 2 页（反面）：白底 A4 + 右下角条形码 + 文字标签。"""
    page = Image.new("RGB", (A4_W_PX, A4_H_PX), "white")
    draw = ImageDraw.Draw(page)

    # 生成条形码图片 + 等比例缩放到 BARCODE_W
    bc_img = _render_barcode_pil(serial_no)
    ratio = BARCODE_W / bc_img.width
    bc_resized = bc_img.resize((BARCODE_W, int(bc_img.height * ratio)), Image.LANCZOS)

    # 右上角留空（用户可能打印时把纸张纵向或横向翻转），只固定右下角
    paste_x = A4_W_PX - CORNER_MARGIN_R - BARCODE_W
    paste_y = A4_H_PX - CORNER_MARGIN_B - BARCODE_H
    page.paste(bc_resized, (paste_x, paste_y))

    # 下方文字标签（serial_no 字符串）
    font = _load_cn_font(size=36)
    label = serial_no
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    label_x = paste_x + (BARCODE_W - tw) // 2
    label_y = paste_y + bc_resized.height + 12
    draw.text((label_x, label_y), label, fill="black", font=font)

    return page


def _build_info_card_page(
    *,
    drawing_no: str,
    name: str,
    serial_no: str | None,
    customer_path: str | None,
) -> Image.Image:
    """渲染第 1 页占位：未上传图纸时给文员/编程员一个明确的信息卡。"""
    page = Image.new("RGB", (A4_W_PX, A4_H_PX), "white")
    draw = ImageDraw.Draw(page)
    title_font = _load_cn_font(size=44)
    body_font = _load_cn_font(size=32)
    small_font = _load_cn_font(size=22)

    # 标题
    title = "零件信息卡"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    draw.text(((A4_W_PX - tw) // 2, 90), title, fill="black", font=title_font)

    # 字段列表
    lines: list[tuple[str, str]] = [
        ("图号", drawing_no or "—"),
        ("名称", name or "—"),
        ("客户", customer_path or "—"),
        ("流水号", serial_no or "—"),
    ]
    y = 220
    for label, value in lines:
        draw.text((120, y), f"{label}:", fill="#444", font=body_font)
        draw.text((260, y), value, fill="black", font=body_font)
        y += 60

    # 提示
    note = "提示：本零件尚未上传图纸，此页面为信息卡占位。如需打印图纸正文，请先在「图纸/文件」中上传 PDF/PNG/JPG 图纸。"
    bbox = draw.textbbox((0, 0), note, font=small_font)
    nw = bbox[2] - bbox[0]
    # 自动换行
    max_chars_per_line = (A4_W_PX - 240) // small_font.size
    chunks = [note[i : i + max_chars_per_line] for i in range(0, len(note), max_chars_per_line)]
    ny = A4_H_PX - 160
    for chunk in chunks:
        bbox = draw.textbbox((0, 0), chunk, font=small_font)
        cw = bbox[2] - bbox[0]
        draw.text(((A4_W_PX - cw) // 2, ny), chunk, fill="#888", font=small_font)
        ny += small_font.size + 8

    return page


def _image_to_a4_pdf_bytes(img: Image.Image) -> bytes:
    """把 pillow Image 渲染成单页 A4 PDF（bytes）。"""
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150.0)
    return buf.getvalue()


async def _download_drawing_bytes(drawing: TDrawingFile) -> bytes:
    """从 COS 拉图纸原始字节。"""
    return await cos_mod.download_object(drawing.object_key)


async def build_part_print_pdf(
    *,
    part_id: int,
    parts: PartRepository,
    drawings: DrawingFileRepository,
) -> bytes:
    """为指定零件生成「图纸 + 条形码」双面打印 PDF，返回 PDF 字节。

    - 找 part 的 master 图纸（`page_index IS NULL`，即装配件主图挂的或
      直接挂到 part 上的图纸；优先取最新一条）。无 → 第 1 页用信息卡占位。
    - PDF 图纸 → 用 pypdf 合并原始页 + 新增的条形码页
    - 图片图纸（PNG/JPG）→ 用 pillow 把图居中铺到 A4 第 1 页 + 条形码第 2 页
    - 其他类型（STEP/DWG/DXF）→ 不可浏览器/纸面直接渲染，回退到信息卡占位
    - 条形码编码内容：`part.serial_no`（无 serial_no 时编码占位 `NO-SERIAL`）
    """
    part = await parts.get_by_id(part_id)
    if part is None:
        raise BizError(
            code=ErrCode.BIZ_PART_NOT_FOUND,
            message=f"part {part_id} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )

    # 取 master 图纸：page_index IS NULL 的最新一条；若无则取任意最新一条
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

    # 解析 serial_no：COMPLETED/CANCELLED 后会被释放为 None
    serial_no = part.serial_no or "NO-SERIAL"

    # 准备反面（条形码页）
    barcode_page_img = _build_barcode_page(serial_no)
    barcode_pdf_bytes = _image_to_a4_pdf_bytes(barcode_page_img)
    barcode_reader = PdfReader(io.BytesIO(barcode_pdf_bytes))

    # 准备正面
    writer = PdfWriter()
    front_pdf_bytes: bytes | None = None
    if master is not None:
        ext = master.file_type.upper()
        if ext == "PDF":
            front_pdf_bytes = await _download_drawing_bytes(master)
        elif ext in {"PNG", "JPG", "JPEG"}:
            try:
                raw = await _download_drawing_bytes(master)
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                # 居中按比例放进 A4
                ratio = min(A4_W_PX / img.width, A4_H_PX / img.height)
                new_w = int(img.width * ratio)
                new_h = int(img.height * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                canvas = Image.new("RGB", (A4_W_PX, A4_H_PX), "white")
                canvas.paste(img, ((A4_W_PX - new_w) // 2, (A4_H_PX - new_h) // 2))
                front_pdf_bytes = _image_to_a4_pdf_bytes(canvas)
            except Exception:  # noqa: BLE001
                _logger.exception("failed to rasterize image drawing, fallback to info card")
                front_pdf_bytes = None
        else:
            # STEP / DWG / DXF：没有 PDF 渲染能力，回退到信息卡
            front_pdf_bytes = None

    if front_pdf_bytes is not None:
        try:
            front_reader = PdfReader(io.BytesIO(front_pdf_bytes))
            for page in front_reader.pages:
                writer.add_page(page)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to merge drawing pdf, fallback to info card")
            front_pdf_bytes = None

    if front_pdf_bytes is None:
        # 拿不到 front PDF（无图、类型不支持、或解析失败）→ 渲染信息卡
        # 拼装 customer_path（轻量：两级 parent_id 关系）
        customer_path: str | None = None
        if part.customer_id is not None:
            # 服务层批量查 part 已经带 customer_name + parent_customer_name
            # 这里我们从 parts 仓库再补一次（调用方已注入 parts）
            # 为减少 N+1，复用 service.part._to_out 的简化路径：直接
            # 通过 PartRepository 查不到名称；走 parts.list_with_filters
            # 也只会回当前 part。所以改成：根据 part 自身 customer_id
            # 通过 parts 拿到 customer_name 与 parent_customer_name
            # （如果仓库有的话），否则显示 None。
            # 简化：仅展示 serial_no / drawing_no / name 三项
            customer_path = None
        info_card = _build_info_card_page(
            drawing_no=part.drawing_no or "",
            name=part.name or "",
            serial_no=part.serial_no,
            customer_path=customer_path,
        )
        front_pdf_bytes = _image_to_a4_pdf_bytes(info_card)
        front_reader = PdfReader(io.BytesIO(front_pdf_bytes))
        for page in front_reader.pages:
            writer.add_page(page)

    # 追加条形码页（第 2 页 / 反面）
    for page in barcode_reader.pages:
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()