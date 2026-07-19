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
from pypdf.generic import RectangleObject

from core import cos as cos_mod
from core.error_code import ErrCode
from core.exception import BizError
from model import TPartFile
from repository.part_file import PartFileRepository
from repository.part import PartRepository
from fastapi import status as http_status

_logger = logging.getLogger(__name__)

# A4 尺寸（pt）：portrait 595×842，landscape 842×595
A4_PORTRAIT = (595, 842)
A4_LANDSCAPE = (842, 595)

# 渲染像素尺寸（@ 150 dpi）
DPI = 150
PX_PER_PT = DPI / 72.0

# === 2026-07-20 迭代 v3：反面页序列号 + 条码 CCW 旋转 90° 沿 A4 右边并排 ===
SERIAL_FONT_PX = int(842 * PX_PER_PT / 8)       # ≈ 219 px（旋转前的字体高度）
BARCODE_H_PX = int(842 * PX_PER_PT / 7)         # ≈ 250 px（旋转前的条码高度；旋转后变成水平宽度）
BARCODE_W_FRACTION = 0.5                          # 旋转前的条码水平宽度 = A4 短边 50%
RIGHT_MARGIN_PT = 28                            # 条码距页面右边（精确 1 cm）
SERIAL_TO_BC_GAP_PT = 22                         # 序列号 ↔ 条码 间距


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
    """渲染反面页（2026-07-20 迭代 v3）：
    - 序列号 + 条码均旋转 90° CCW（rotate(90)），沿 A4 右边并排堆叠；
    - 序列号在条码左侧（视觉上的「左」，即 x 较小的位置）；
    - 条码水平长度 = A4 短边 50%（旋转后变成纵向长度）；
    - 序列号字号 = A4 长边 / 8（旋转后是文本纵向高度）；
    - 条码距页面右边 RIGHT_MARGIN_PT = 28 pt（精确 1 cm）。

    旋转方向说明：rotate(90) 是 PIL 逆时针 90°，原始 LR 文本 → 旋转后
    文本最右字符（如 "F1004" 的 "4"）出现在顶部，自上而下读为 "4001F"。
    这是用户指定「向左旋转 90°」的字面解释；扫描方面两个方向现代扫码枪都兼容。
    """
    page_w, page_h = _a4_px(orientation)
    page = Image.new("RGB", (page_w, page_h), "white")

    short_side_px = min(page_w, page_h)

    # === 条码：先按原朝向渲染 + 缩放，再旋转 90° CCW ===
    bc_img = _render_barcode_pil(serial_no)
    target_bc_w_native = int(short_side_px * BARCODE_W_FRACTION)
    bc_native_h_scaled = int(bc_img.height * target_bc_w_native / bc_img.width)
    bc_resized = bc_img.resize(
        (target_bc_w_native, bc_native_h_scaled),
        Image.LANCZOS,
    )
    # NEAREST 保持条码边缘锐利，确保扫码兼容性
    bc_rotated = bc_resized.rotate(90, expand=True, resample=Image.NEAREST)

    # === 序列号：渲染到刚好装下的白色画布，再旋转 90° CCW ===
    serial_font = _load_cn_font(size=SERIAL_FONT_PX)
    _tmp = Image.new("RGB", (1, 1))
    _tmp_draw = ImageDraw.Draw(_tmp)
    bbox = _tmp_draw.textbbox((0, 0), serial_no, font=serial_font)
    serial_w_native = bbox[2] - bbox[0]
    serial_h_native = bbox[3] - bbox[1]

    serial_canvas = Image.new("RGB", (serial_w_native, serial_h_native), "white")
    serial_draw = ImageDraw.Draw(serial_canvas)
    # y 偏移 -bbox[1] 处理 ascender 顶部负偏移，确保字符不超出画布
    serial_draw.text((0, -bbox[1]), serial_no, fill="#000", font=serial_font)
    # BICUBIC 让字符边缘平滑（可读性优先）
    serial_rotated = serial_canvas.rotate(90, expand=True, resample=Image.BICUBIC)

    # === 布局：旋转后两块沿右边并排堆叠，垂直居中 ===
    right_margin_px = int(RIGHT_MARGIN_PT * PX_PER_PT)
    gap_px = int(SERIAL_TO_BC_GAP_PT * PX_PER_PT)

    bc_w, bc_h = bc_rotated.size
    sr_w, sr_h = serial_rotated.size

    # 条码贴在右边（距右边 1 cm），序列号位于条码左侧
    bc_right_x = page_w - right_margin_px
    bc_x = bc_right_x - bc_w

    # 序列号左侧贴条码：右缘 = bc_x - gap
    sr_right_x = bc_x - gap_px
    sr_x = sr_right_x - sr_w

    # 垂直方向：以条码高度为基准，整体垂直居中
    # 条码竖条更高（620px），序列号竖条更短（290px），两者按各自高度居中于同一 y_center
    block_h = max(bc_h, sr_h)
    y_center = page_h // 2
    bc_y = y_center - bc_h // 2
    sr_y = y_center - sr_h // 2

    page.paste(serial_rotated, (sr_x, sr_y))
    page.paste(bc_rotated, (bc_x, bc_y))

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
            "如需打印图纸正文，请先在「图纸/文件」中上传 PDF / PNG / JPG / "
            "GIF / BMP / TIFF / WEBP / HEIC 任一格式图纸。")
    max_chars_per_line = (page_w - 240) // small_font.size
    chunks = [note[i : i + max_chars_per_line] for i in range(0, len(note), max_chars_per_line)]
    ny = page_h - 180
    for chunk in chunks:
        bbox = draw.textbbox((0, 0), chunk, font=small_font)
        cw = bbox[2] - bbox[0]
        draw.text(((page_w - cw) // 2, ny), chunk, fill="#888", font=small_font)
        ny += small_font.size + 8

    return page


def _image_to_a4_pdf_bytes(
    img: Image.Image, orientation: str = "landscape",
) -> bytes:
    """把 pillow Image 渲染成单页 PDF（bytes）。

    2026-07-20 迭代：返回前用 PdfReader 读出再用 PdfWriter 覆写
    MediaBox/CropBox/TrimBox/BleedBox 为精确 A4，避免 Pillow 按
    resolution 推算出的 mediabox 偏差，也避免源 PDF 的非标 CropBox
    导致浏览器打印预览按 CropBox 裁切图纸。
    """
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=DPI)
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    w_pt, h_pt = A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
    box = RectangleObject([0, 0, w_pt, h_pt])
    for page in writer.pages:
        page.mediabox = box
        page.cropbox = box
        page.trimbox = box
        page.bleedbox = box
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


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


async def _download_drawing_bytes(drawing: TPartFile) -> bytes:
    """从 COS 拉图纸原始字节。"""
    return await cos_mod.download_object(drawing.object_key)


# ============================================================
# 主入口
# ============================================================
async def build_part_print_pdf(
    *,
    part_id: int,
    parts: PartRepository,
    part_files: PartFileRepository,
) -> bytes:
    """为指定零件生成「图纸 + 条形码」双面打印 PDF。

    朝向：自动跟随上传图纸的 orientation（landscape 优先，因图纸常用横向）；
    无图纸时默认 landscape。条码页 / 信息卡页与图纸页朝向一致，保证双面
    打印翻转方向正确。

    2026-07-10 起：图纸存储统一到 `t_part_file` (kind=DRAWING)。
    """
    part = await parts.get_by_id(part_id)
    if part is None:
        raise BizError(
            code=ErrCode.BIZ_PART_NOT_FOUND,
            message=f"part {part_id} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )

    # 取 master 图纸：kind=DRAWING 的最新一条
    drawings_for_part = await part_files.list_by_part(part_id, kind="DRAWING")
    master: TPartFile | None = drawings_for_part[0] if drawings_for_part else None

    serial_no = part.serial_no or "NO-SERIAL"

    # ---- 准备正面（图纸）----
    front_pdf_bytes: bytes | None = None
    orientation: str = "landscape"  # 默认横向（图纸常用）
    if master is not None:
        ext = master.file_type.upper()
        try:
            if ext == "PDF":
                front_pdf_bytes = await _download_drawing_bytes(master)
                # 2026-07-20 调试：源 PDF 若 CropBox != MediaBox，浏览器按 CropBox 渲染
                # 会裁切图纸。此处打 warning 辅助未来类似问题定位。
                try:
                    _r = PdfReader(io.BytesIO(front_pdf_bytes))
                    if _r.pages:
                        _mb = _r.pages[0].mediabox
                        _cb = _r.pages[0].cropbox
                        if (float(_cb.width) != float(_mb.width)
                                or float(_cb.height) != float(_mb.height)):
                            _logger.warning(
                                "source drawing PDF has CropBox != MediaBox: "
                                "mb=%s cb=%s — normalizing",
                                [float(x) for x in _mb],
                                [float(x) for x in _cb],
                            )
                except Exception:  # noqa: BLE001
                    pass
                orientation = _detect_pdf_orientation(front_pdf_bytes)
            elif ext in {
                "PNG", "JPG", "JPEG", "GIF", "BMP",
                "TIF", "TIFF", "WEBP",
            }:
                # 2026-07-14：扩所有 DRAWING 接受的图片格式
                # pillow 原生支持 PNG/JPEG/GIF/BMP/TIFF/WEBP；HEIC 走单独 try 分支
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
                front_pdf_bytes = _image_to_a4_pdf_bytes(canvas, orientation)
            elif ext == "HEIC":
                # HEIC 需 pillow-heif；运行时 try，缺失则降级到信息卡
                raw = await _download_drawing_bytes(master)
                try:
                    from pillow_heif import register_heif_opener  # noqa: WPS433
                    register_heif_opener()
                    img = Image.open(io.BytesIO(raw)).convert("RGB")
                    orientation = _detect_image_orientation(img)
                    page_w_px, page_h_px = _a4_px(orientation)
                    ratio = min(page_w_px / img.width, page_h_px / img.height)
                    new_w = int(img.width * ratio)
                    new_h = int(img.height * ratio)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    canvas = Image.new("RGB", (page_w_px, page_h_px), "white")
                    canvas.paste(img, ((page_w_px - new_w) // 2, (page_h_px - new_h) // 2))
                    front_pdf_bytes = _image_to_a4_pdf_bytes(canvas, orientation)
                except ImportError:
                    _logger.warning(
                        "pillow-heif not installed; HEIC drawing falls back to info card"
                    )
                    front_pdf_bytes = None
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
        info_pdf_bytes = _image_to_a4_pdf_bytes(info_card, orientation)
        info_reader = PdfReader(io.BytesIO(info_pdf_bytes))
        for page in info_reader.pages:
            writer.add_page(page)

    # 反面：条码页（朝向与正面一致）
    barcode_page_img = _build_barcode_page(orientation, serial_no)
    barcode_pdf_bytes = _image_to_a4_pdf_bytes(barcode_page_img, orientation)
    barcode_reader = PdfReader(io.BytesIO(barcode_pdf_bytes))
    for page in barcode_reader.pages:
        writer.add_page(page)

    # ---- 规范化所有 page 的 boxes 为精确 A4 ----
    # 2026-07-20 修复：源 PDF（如 CAD 导出的 PDF）经常带非标 CropBox/TrimBox，
    # 浏览器原生打印预览有时按 CropBox 而非 MediaBox 渲染，导致图纸被裁切显示不全。
    # 强制把每页的 mediabox / cropbox / trimbox / bleedbox 都对齐到精确 A4。
    _w_pt, _h_pt = A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
    _a4_box = RectangleObject([0, 0, _w_pt, _h_pt])
    for _p in writer.pages:
        _p.mediabox = _a4_box
        _p.cropbox = _a4_box
        _p.trimbox = _a4_box
        _p.bleedbox = _a4_box

    # 2026-07-20 调试：输出每页最终的 mediabox 大小（pt），
    # 辅助排查「打印预览显示非 A4」类问题。
    _logger.info(
        "build_part_print_pdf: orientation=%s pages=%d A4=%sx%s pt",
        orientation,
        len(writer.pages),
        _w_pt,
        _h_pt,
    )
    for _i, _p in enumerate(writer.pages):
        _logger.info(
            "  page[%d] mediabox=%sx%s cropbox=%sx%s",
            _i,
            float(_p.mediabox.width),
            float(_p.mediabox.height),
            float(_p.cropbox.width),
            float(_p.cropbox.height),
        )

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ============================================================
# 批量入口
# ============================================================
async def build_parts_print_pdf_batch(
    *,
    part_ids: list[int],
    parts: PartRepository,
    part_files: PartFileRepository,
) -> bytes:
    """合并多个零件的双面 PDF 为单 PDF 字节流（2026-07-17 批量打印）。

    - 顺序：按传入 part_ids 顺序逐个拼接（每 part 2 页：图页 + 条码页）。
    - 失败处理：单 part 失败仅 warning 日志 + 跳过该 part，不阻断整批。
      （用户至少能拿到其余图纸；失败的 detail 在后端日志排查。）
    - 空集合：返回有效空 PDF（PdfWriter 0 page，PDF reader 仍可解析）。

    用于前端「批量打印图纸」一次弹单次打印对话框：
        POST /parts/print-drawing-batch  body: { part_ids: ["..."] }
    """
    logger = logging.getLogger(__name__)
    writer = PdfWriter()
    for pid in part_ids:
        try:
            pdf_bytes = await build_part_print_pdf(
                part_id=pid, parts=parts, part_files=part_files,
            )
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:
            # 单 part 失败跳过；常见：part 不存在 / 已软删 / 文件 COS 404
            logger.warning("batch print skip part_id=%s: %s", pid, e)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()