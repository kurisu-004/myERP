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
import functools
import io
import logging
from dataclasses import dataclass

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from core import cos as cos_mod
from core.error_code import ErrCode
from core.exception import BizError
from model import TAssembly, TPartFile
from repository.assembly import AssemblyRepository
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

# 并发下载限制：8 个并发 COS 请求。
# 选型理由：COS 服务端连接数无瓶颈，但 asyncio+线程池 并发过大易导致内存峰值暴涨；
# 8 是「CPU 并行渲染线程池 default min(32, cpu+4)」的 2 倍左右，保证 IO 不饿死 CPU。
_MAX_CONCURRENT_DOWNLOADS = 8

def _render_small_barcode_with_serial(
    serial_no: str,
) -> tuple[Image.Image, Image.Image]:
    """2026-07-24 新增：渲染一组「小条码 + 小序列号」PIL Image（未旋转）。

    复用 `_render_barcode_pil` 与 `_load_cn_font`；条码水平宽度 =
    A4 短边 × SMALL_BC_W_FRACTION，序列号字体 SMALL_SERIAL_FONT_PX。
    返回 (barcode_pil, serial_pil)，两者都是 RGB 白底图。
    """
    short_side_px = int(min(A4_LANDSCAPE) * PX_PER_PT)
    bc_img = _render_barcode_pil(serial_no)
    target_bc_w = int(short_side_px * SMALL_BC_W_FRACTION)
    bc_h_scaled = int(bc_img.height * target_bc_w / bc_img.width)
    bc_pil = bc_img.resize((target_bc_w, bc_h_scaled), Image.LANCZOS)

    serial_font = _load_cn_font(size=SMALL_SERIAL_FONT_PX)
    _tmp = Image.new("RGB", (1, 1))
    bbox = ImageDraw.Draw(_tmp).textbbox((0, 0), serial_no, font=serial_font)
    sw, sh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    serial_canvas = Image.new("RGB", (sw, sh), "white")
    ImageDraw.Draw(serial_canvas).text((0, -bbox[1]), serial_no, fill="#000", font=serial_font)
    return bc_pil, serial_canvas


# === 2026-07-20 迭代 v3：反面页序列号 + 条码 CCW 旋转 90° 沿 A4 右边并排 ===
SERIAL_FONT_PX = int(842 * PX_PER_PT / 8)       # ≈ 219 px（旋转前的字体高度）
BARCODE_H_PX = int(842 * PX_PER_PT / 7)         # ≈ 250 px（旋转前的条码高度；旋转后变成水平宽度）
BARCODE_W_FRACTION = 0.5                          # 旋转前的条码水平宽度 = A4 短边 50%
RIGHT_MARGIN_PT = 28                            # 条码距页面右边（精确 1 cm）
SERIAL_TO_BC_GAP_PT = 22                         # 序列号 ↔ 条码 间距

# === 2026-07-24 新增：备用小条码（防图纸污染无法扫码）===
SMALL_SERIAL_FONT_PX = 65                      # 小序列号字体（@150 DPI）
SMALL_BC_W_FRACTION = 0.18                     # 小条码水平宽度 = A4 短边 18%（主条码 50%）
SMALL_LEFT_MARGIN_PT = 28                      # 距页面左边（精确 1 cm）
SMALL_BOTTOM_MARGIN_PT = 28                    # 距页面下边
SMALL_TOP_MARGIN_PT = 28                       # 距页面上边
SMALL_SERIAL_GAP_PT = 10                       # 小序列号 ↔ 小条码 间距


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


@functools.lru_cache(maxsize=32)
def _load_cn_font(size: int) -> ImageFont.ImageFont:
    """尽量加载中文字体；找不到时 fallback 到默认（标签仍可显示）。

    部署环境（alpine）默认无任何字体，必须显式 apk add wqy-microhei 装入，
    否则 fallback 到 PIL 内置 ~10px bitmap，导致序列号变得极小。
    """
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        # Alpine apk add font-dejavu 安装位置
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        # Debian/Ubuntu apt install fonts-dejavu 路径
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # CJK 字体（若后续需要中文渲染,装 font-noto-cjk 后命中）
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
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

    # === 2026-07-24 新增：2 个备用小条形码 + 序列号 ===
    # 防图纸污染（涂污/折角）无法扫码：左下方水平放、左上方旋转 180° 放，
    # 用户从不同角度扫描都能命中至少一组。
    bc_small, serial_small = _render_small_barcode_with_serial(serial_no)
    bc_s_w, bc_s_h = bc_small.size
    sr_s_w, sr_s_h = serial_small.size
    left_px = int(SMALL_LEFT_MARGIN_PT * PX_PER_PT)
    bottom_px = int(SMALL_BOTTOM_MARGIN_PT * PX_PER_PT)
    top_px = int(SMALL_TOP_MARGIN_PT * PX_PER_PT)
    gap_s_px = int(SMALL_SERIAL_GAP_PT * PX_PER_PT)

    # ---- 左下方：水平放置（不旋转），序列号在上、条码在下 ----
    serial_y = page_h - bottom_px - bc_s_h - gap_s_px - sr_s_h
    page.paste(serial_small, (left_px, serial_y))
    page.paste(bc_small, (left_px, page_h - bottom_px - bc_s_h))

    # ---- 左上方：旋转 180° 放置 ----
    # 先把"水平放置"的小组合成到独立画布，再 rotate(180)，最后贴到左上角
    mini_w = max(sr_s_w, bc_s_w)
    mini_h = sr_s_h + gap_s_px + bc_s_h
    mini_canvas = Image.new("RGB", (mini_w, mini_h), "white")
    mini_canvas.paste(serial_small, (0, 0))
    mini_canvas.paste(bc_small, (0, sr_s_h + gap_s_px))
    mini_rotated = mini_canvas.rotate(180, expand=True, resample=Image.BICUBIC)
    page.paste(mini_rotated, (left_px, top_px))

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
    """读 PDF 第一页有效页面尺寸判断朝向。"""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not reader.pages:
            return "landscape"
        page = reader.pages[0]
        box = page.mediabox
        width, height = float(box.width), float(box.height)
        if page.rotation % 180:
            width, height = height, width
        return "landscape" if width > height else "portrait"
    except Exception:  # noqa: BLE001
        _logger.exception("failed to detect pdf orientation, default to landscape")
        return "landscape"


def _fit_pdf_page_to_a4(page: PageObject, orientation: str) -> None:
    """将 PDF 页面内容等比缩放并居中到精确 A4 页面。"""
    # 把 /Rotate 合入内容流，后续可统一按实际可视宽高计算缩放与平移。
    if page.rotation:
        page.transfer_rotation_to_content()

    source_box = page.mediabox
    source_width = float(source_box.width)
    source_height = float(source_box.height)
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source PDF page has invalid dimensions")

    target_width, target_height = (
        A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
    )
    scale = min(1.0, target_width / source_width, target_height / source_height)
    fitted_width = source_width * scale
    fitted_height = source_height * scale
    offset_x = (target_width - fitted_width) / 2
    offset_y = (target_height - fitted_height) / 2

    transform = (
        Transformation()
        .translate(tx=-float(source_box.left), ty=-float(source_box.bottom))
        .scale(sx=scale, sy=scale)
        .translate(tx=offset_x, ty=offset_y)
    )
    page.add_transformation(transform)

    target_box = [0, 0, target_width, target_height]
    page.mediabox = RectangleObject(target_box)
    page.cropbox = RectangleObject(target_box)
    page.trimbox = RectangleObject(target_box)
    page.bleedbox = RectangleObject(target_box)


def _detect_image_orientation(img: Image.Image) -> str:
    return "landscape" if img.width > img.height else "portrait"


async def _download_drawing_bytes(drawing: TPartFile) -> bytes:
    """从 COS 拉图纸原始字节（带进程内缓存，按 content_sha256 命中）。"""
    return await cos_mod.download_object_cached(drawing.object_key, drawing.content_sha256)


# ============================================================
# 数据准备 / 纯渲染拆分（2026-07-30 批量并行化引入）
# ============================================================
@dataclass
class _PartPrintData:
    """打印一项的纯 Python 数据容器（sync 渲染侧禁止传递 ORM 对象）。"""

    part_id: int
    serial_no: str
    drawing_no: str
    name: str
    customer_path: str | None = None
    drawing_bytes: bytes | None = None
    drawing_ext: str | None = None
    orientation: str = "landscape"


async def _prepare_part_print_data(
    *,
    part_id: int,
    parts: PartRepository,
    part_files: PartFileRepository,
) -> _PartPrintData:
    """async 阶段：DB 查询 + COS 下载原始字节。返回纯数据，不碰 PIL/pypdf。

    朝向检测与图片→PDF 转换延迟到 sync 阶段 `_build_part_print_pdf_sync`，
    保证批量路径的 async 侧只做 IO，不阻塞事件循环。
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
    drawing_bytes: bytes | None = None
    drawing_ext: str | None = None

    if master is not None:
        ext = master.file_type.upper()
        drawing_ext = ext
        try:
            if ext in {
                "PDF", "PNG", "JPG", "JPEG", "GIF", "BMP",
                "TIF", "TIFF", "WEBP", "HEIC",
            }:
                drawing_bytes = await _download_drawing_bytes(master)
            else:
                # STEP / DWG / DXF：纸面不可直接渲染 → info card
                drawing_bytes = None
        except Exception:  # noqa: BLE001
            _logger.exception("failed to download master drawing, fallback to info card")
            drawing_bytes = None

    return _PartPrintData(
        part_id=part_id,
        serial_no=serial_no,
        drawing_no=part.drawing_no or "",
        name=part.name or "",
        drawing_bytes=drawing_bytes,
        drawing_ext=drawing_ext,
        orientation="landscape",
    )


def _build_part_print_pdf_sync(data: _PartPrintData) -> bytes:
    """sync 阶段：纯 PIL/pypdf/条码渲染（禁止访问 ORM / session）。"""
    front_pdf_bytes: bytes | None = data.drawing_bytes
    orientation: str = data.orientation

    # 批次路径未在 async 侧做朝向检测/图片转换，在此处补做
    if front_pdf_bytes is not None and data.drawing_ext:
        ext = data.drawing_ext.upper()
        if ext == "PDF":
            orientation = _detect_pdf_orientation(front_pdf_bytes)
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
        elif ext in {
            "PNG", "JPG", "JPEG", "GIF", "BMP",
            "TIF", "TIFF", "WEBP",
        }:
            try:
                img = Image.open(io.BytesIO(front_pdf_bytes)).convert("RGB")
                orientation = _detect_image_orientation(img)
                page_w_px, page_h_px = _a4_px(orientation)
                ratio = min(page_w_px / img.width, page_h_px / img.height)
                new_w = int(img.width * ratio)
                new_h = int(img.height * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                canvas = Image.new("RGB", (page_w_px, page_h_px), "white")
                canvas.paste(img, ((page_w_px - new_w) // 2, (page_h_px - new_h) // 2))
                front_pdf_bytes = _image_to_a4_pdf_bytes(canvas, orientation)
            except Exception:  # noqa: BLE001
                _logger.exception("failed to process image drawing, fallback to info card")
                front_pdf_bytes = None
        elif ext == "HEIC":
            try:
                from pillow_heif import register_heif_opener  # noqa: WPS433
                register_heif_opener()
                img = Image.open(io.BytesIO(front_pdf_bytes)).convert("RGB")
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
            except Exception:  # noqa: BLE001
                _logger.exception("failed to process HEIC drawing, fallback to info card")
                front_pdf_bytes = None
        else:
            front_pdf_bytes = None

    writer = _build_drawing_with_barcode_pages(
        drawing_bytes=front_pdf_bytes,
        serial_no=data.serial_no,
        drawing_no=data.drawing_no,
        name=data.name,
        orientation=orientation,
    )

    _w_pt, _h_pt = A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
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


def _build_drawing_with_barcode_pages(
    *,
    drawing_bytes: bytes | None,
    serial_no: str,
    drawing_no: str,
    name: str,
    orientation: str = "landscape",
) -> PdfWriter:
    """构建「图纸页 + 条码背面页」双面 PDF（零件/装配体通用）。

    - drawing_bytes 为 None 时退化为信息卡占位。
    - 条码页强制 landscape，与图纸朝向解耦。
    """
    writer = PdfWriter()

    # 正面：原 PDF 直接合并；否则信息卡占位
    if drawing_bytes is not None:
        try:
            front_reader = PdfReader(io.BytesIO(drawing_bytes))
            for page in front_reader.pages:
                writer.add_page(page)
                _fit_pdf_page_to_a4(writer.pages[-1], orientation)
        except Exception:  # noqa: BLE001
            _logger.exception("failed to merge drawing pdf, fallback to info card")
            drawing_bytes = None

    if drawing_bytes is None:
        info_card = _build_info_card_page(
            orientation=orientation,
            drawing_no=drawing_no,
            name=name,
            serial_no=serial_no,
            customer_path=None,
        )
        info_pdf_bytes = _image_to_a4_pdf_bytes(info_card, orientation)
        info_reader = PdfReader(io.BytesIO(info_pdf_bytes))
        for page in info_reader.pages:
            writer.add_page(page)

    # 反面：条码页（强制 landscape）
    bc_orientation = "landscape"
    barcode_page_img = _build_barcode_page(bc_orientation, serial_no)
    barcode_pdf_bytes = _image_to_a4_pdf_bytes(barcode_page_img, bc_orientation)
    barcode_reader = PdfReader(io.BytesIO(barcode_pdf_bytes))
    for page in barcode_reader.pages:
        writer.add_page(page)

    # 规范化所有 page 的 boxes 为精确 A4
    _w_pt, _h_pt = A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
    _a4_box = RectangleObject([0, 0, _w_pt, _h_pt])
    _w_pt_bc, _h_pt_bc = A4_LANDSCAPE
    _a4_box_bc = RectangleObject([0, 0, _w_pt_bc, _h_pt_bc])
    if writer.pages:
        _p_bc = writer.pages[-1]
        _p_bc.mediabox = _a4_box_bc
        _p_bc.cropbox = _a4_box_bc
        _p_bc.trimbox = _a4_box_bc
        _p_bc.bleedbox = _a4_box_bc
        for _p in writer.pages[:-1]:
            _p.mediabox = _a4_box
            _p.cropbox = _a4_box
            _p.trimbox = _a4_box
            _p.bleedbox = _a4_box

    return writer


# ============================================================
# 主入口（兼容层：保持返回 bytes，内部走 _prepare + _sync）
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
    2026-07-30 起：内部拆分为 async 数据准备 + sync 纯渲染，供批量并行复用。
    """
    data = await _prepare_part_print_data(
        part_id=part_id, parts=parts, part_files=part_files,
    )
    return _build_part_print_pdf_sync(data)


# ============================================================
# 批量入口（2026-07-30 重构：两阶段流水线 — IO 并发 + CPU 并行）
# ============================================================
async def build_parts_print_pdf_batch(
    *,
    part_ids: list[int],
    assembly_ids: list[int] | None = None,
    parts: PartRepository,
    part_files: PartFileRepository,
    assemblies: AssemblyRepository | None = None,
) -> bytes:
    """合并多个零件的双面 PDF 为单 PDF 字节流（2026-07-17 批量打印；2026-07-30 扩展装配件）。

    - 顺序：按传入 part_ids 顺序逐个拼接（每 part 2 页：图页 + 条码页）。
    - 装配体：若 part_ids 包含某装配件子件，或显式传入 assembly_ids，则在该装配件
      首个子件前插入总装图页（kind=ASSEMBLY_MASTER）+ 条码背面页，每装配件仅一次。
    - assembly_ids 额外打印该装配件的**全部子件**（与 part_ids 子件去重）。
    - 失败处理：单 part / 单装配体失败仅 warning 日志 + 跳过，不阻断整批。
    - 空集合：返回有效空 PDF（PdfWriter 0 page，PDF reader 仍可解析）。
    """
    logger = logging.getLogger(__name__)

    # 1. 拉取所有选中的零件
    selected_parts = await parts.list_by_ids(part_ids)

    # 2. 建立「需要打印总装图的装配体」→「待打印子件集合」映射
    assembly_to_children: dict[int, dict[int, "TPart"]] = {}
    standalone_parts: list["TPart"] = []

    for p in selected_parts:
        if p.assembly_id is not None:
            assembly_to_children.setdefault(p.assembly_id, {})[p.id] = p
        else:
            standalone_parts.append(p)

    # 3. assembly_ids 显式指定：追加该装配件全部子件 + 总装图
    if assembly_ids and assemblies is not None:
        for aid in assembly_ids:
            children = await parts.list_children(aid)
            child_map = assembly_to_children.setdefault(aid, {})
            for c in children:
                if c.id not in child_map:
                    child_map[c.id] = c

    # 4. 构建打印顺序：先 standalone parts，再 assembly（总装图 + 子件）
    standalone_ids = {p.id for p in standalone_parts}
    ordered_part_ids: list[int] = [pid for pid in part_ids if pid in standalone_ids]

    assembly_order: list[int] = []
    seen_asm = set()
    for p in selected_parts:
        if p.assembly_id is not None and p.assembly_id not in seen_asm:
            assembly_order.append(p.assembly_id)
            seen_asm.add(p.assembly_id)
    if assembly_ids:
        for aid in assembly_ids:
            if aid not in seen_asm:
                assembly_order.append(aid)
                seen_asm.add(aid)

    # 5. 收集所有需要查询的 part_id（standalone + children）和 assembly_id
    child_part_ids: list[int] = []
    for aid in assembly_order:
        child_map = assembly_to_children.get(aid, {})
        children = sorted(child_map.values(), key=lambda c: (c.drawing_no or "", c.id))
        for c in children:
            child_part_ids.append(c.id)

    all_part_ids_for_db = list(set(ordered_part_ids + child_part_ids))
    all_parts_map: dict[int, "TPart"] = {}
    if all_part_ids_for_db:
        all_parts = await parts.list_by_ids(all_part_ids_for_db)
        all_parts_map = {p.id: p for p in all_parts}

    # 6. 批量预取图纸元数据（N+1 → 2 次查询）
    drawing_metadata: dict[int, "TPartFile" | None] = {}
    if all_part_ids_for_db:
        drawing_metadata = await part_files.list_by_parts(all_part_ids_for_db, kind="DRAWING")

    assembly_master_metadata: dict[int, "TPartFile" | None] = {}
    if assembly_order:
        assembly_master_metadata = await part_files.list_by_parts(assembly_order, kind="ASSEMBLY_MASTER")

    # 7. 并发下载全部图纸（Semaphore 限流）
    sem = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)

    async def _download_with_sem(file_row: "TPartFile") -> bytes | None:
        async with sem:
            try:
                return await _download_drawing_bytes(file_row)
            except Exception as e:  # noqa: BLE001
                logger.warning("batch print download skip %s: %s", file_row.object_key, e)
                return None

    download_tasks = []
    download_keys: list[tuple[str, int]] = []  # (type, id)

    for pid in ordered_part_ids:
        if pid in drawing_metadata:
            download_tasks.append(_download_with_sem(drawing_metadata[pid]))
            download_keys.append(("part", pid))

    for aid in assembly_order:
        if aid in assembly_master_metadata:
            download_tasks.append(_download_with_sem(assembly_master_metadata[aid]))
            download_keys.append(("asm_master", aid))

    for aid in assembly_order:
        child_map = assembly_to_children.get(aid, {})
        children = sorted(child_map.values(), key=lambda c: (c.drawing_no or "", c.id))
        for c in children:
            if c.id in drawing_metadata:
                download_tasks.append(_download_with_sem(drawing_metadata[c.id]))
                download_keys.append(("part", c.id))

    download_results = await asyncio.gather(*download_tasks, return_exceptions=True)

    part_drawing_bytes: dict[int, bytes | None] = {}
    asm_master_bytes: dict[int, bytes | None] = {}
    for idx, result in enumerate(download_results):
        item_type, item_id = download_keys[idx]
        if isinstance(result, Exception):
            logger.warning("batch print download failed for %s %s: %s", item_type, item_id, result)
            result = None
        if item_type == "part":
            part_drawing_bytes[item_id] = result
        else:
            asm_master_bytes[item_id] = result

    # 8. 构建 _PartPrintData 列表（保持打印顺序）
    items_data: list[_PartPrintData] = []

    for pid in ordered_part_ids:
        p = all_parts_map.get(pid)
        if p is None:
            continue
        master = drawing_metadata.get(pid)
        items_data.append(
            _PartPrintData(
                part_id=pid,
                serial_no=p.serial_no or "NO-SERIAL",
                drawing_no=p.drawing_no or "",
                name=p.name or "",
                drawing_bytes=part_drawing_bytes.get(pid),
                drawing_ext=master.file_type.upper() if master else None,
                orientation="landscape",
            )
        )

    for aid in assembly_order:
        asm = await assemblies.get_by_id(aid) if assemblies else None
        master = assembly_master_metadata.get(aid)
        items_data.append(
            _PartPrintData(
                part_id=aid,
                serial_no=asm.serial_no if asm else "NO-SERIAL",
                drawing_no=asm.drawing_no if asm else "",
                name=asm.name if asm else "",
                drawing_bytes=asm_master_bytes.get(aid),
                drawing_ext=master.file_type.upper() if master else None,
                orientation="landscape",
            )
        )

        child_map = assembly_to_children.get(aid, {})
        children = sorted(child_map.values(), key=lambda c: (c.drawing_no or "", c.id))
        for c in children:
            child_master = drawing_metadata.get(c.id)
            items_data.append(
                _PartPrintData(
                    part_id=c.id,
                    serial_no=c.serial_no or "NO-SERIAL",
                    drawing_no=c.drawing_no or "",
                    name=c.name or "",
                    drawing_bytes=part_drawing_bytes.get(c.id),
                    drawing_ext=child_master.file_type.upper() if child_master else None,
                    orientation="landscape",
                )
            )

    # 9. CPU 并行渲染（丢线程池）
    render_tasks = [
        asyncio.to_thread(_build_part_print_pdf_sync, data)
        for data in items_data
    ]
    render_results = await asyncio.gather(*render_tasks, return_exceptions=True)

    # 10. 按原始顺序合并
    writer = PdfWriter()
    for result in render_results:
        if isinstance(result, Exception):
            logger.warning("batch print render skip: %s", result)
            continue
        try:
            reader = PdfReader(io.BytesIO(result))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:  # noqa: BLE001
            logger.warning("batch print merge skip: %s", e)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
