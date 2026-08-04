"""零件打印 PDF 合成 service。

为「订单详情 → 图纸/文件 → 打印」按钮生成的双面打印 PDF：
- 第 1 页 = 图纸（正面：上传的那一张；若无图纸则渲染一张信息卡占位）
- 第 2 页 = 条形码，反面固定角落位置（默认右下角），编码 `serial_no`

方向处理：自动检测上传 PDF / 图片的宽高比，图纸页是横向 A4 则条码页
也用横向 A4（保持打印机双面翻转方向一致）。无图纸时按零件原始朝向信息
默认横向（图纸常用横向）；最终落 A4（595×842 pt 或 842×595 pt）。

依赖（**全部跨平台**，部署在 Linux Docker 无任何影响）：
- `pillow`：图片/PDF 渲染（信息卡占位 + 图片输入规格化）
- `pikepdf`：合并 PDF 页面（C++ QPDF 后端，快 10-50× 且释放 GIL）
- `pypdfium2`：把矢量 PDF 光栅化到 JPEG（PDFium 后端，wheel 自带二进制）
- `reportlab`：背面页矢量直出 PDF（内置 Code128 矢量条码 + TTFont）
- `pypdf`：保留依赖（utils/pdf.py 批量导入拆页、service/assembly.py PDF
           校验在用），打印热路径不再调用
- `python-barcode`：保留依赖（utils/gen_* 工牌脚本），同上

2026-07-31 打印性能优化引入：
- 背面页改 ReportLab 矢量直出（`service/_print_back_page.py`），从 ~150 KB
  光栅降到 ~30-50 KB 矢量，~0.5s/件 → ~5ms/件。
- 正面页规格化 + 两级缓存（`service/_print_front_cache.py`），按
  content_sha256 缓存 200 DPI 单页 JPEG PDF，L1 本地磁盘 LRU（1 GB），
  L2 COS `printcache/`（持久）；重复打印近乎零计算。
- pikepdf 合并替换 pypdf；并发数从 .env 的 APP_CPU_CORES 派生。
- ?vector=1 query 参数走原 passthrough（个别图纸光栅化不清晰时用）。
"""
from __future__ import annotations

import asyncio
import functools
import io
import logging
import time
from dataclasses import dataclass
from datetime import date

from PIL import Image, ImageDraw, ImageFont
import pikepdf
from pypdf import PageObject, Transformation
from pypdf.generic import RectangleObject

from core import cos as cos_mod
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from model import TAssembly, TPartFile
from repository.assembly import AssemblyRepository
from repository.part_file import PartFileRepository
from repository.part import PartRepository
from fastapi import status as http_status

# 2026-07-31 新模块（拆分后更易复用与单测）
from service._print_back_page import _build_back_page_pdf
from service._print_front_cache import (
    FrontCacheResult,
    get_normalized_front_pdf,
)

_logger = logging.getLogger(__name__)

# A4 尺寸（pt）：portrait 595×842，landscape 842×595
A4_PORTRAIT = (595, 842)
A4_LANDSCAPE = (842, 595)

# 渲染像素尺寸（信息卡占位用，@ 150 dpi）
DPI = 150
PX_PER_PT = DPI / 72.0


def _a4_px(orientation: str) -> tuple[int, int]:
    """按方向返回 A4 像素尺寸 (w, h)。orientation ∈ {'portrait','landscape'}。"""
    w_pt, h_pt = A4_LANDSCAPE if orientation == "landscape" else A4_PORTRAIT
    return (int(w_pt * PX_PER_PT), int(h_pt * PX_PER_PT))


# ============================================================
# 字体（仅信息卡占位页 + vector=1 图片输入路径在用）
# ============================================================
@functools.lru_cache(maxsize=32)
def _load_cn_font(size: int) -> ImageFont.ImageFont:
    """尽量加载中文字体；找不到时 fallback 到默认（标签仍可显示）。

    部署环境（alpine）默认无任何字体；2026-07-31 Dockerfile 加装
    font-wqy-microhei 后信息卡中文可正常渲染。候选路径覆盖 Alpine / Debian /
    Ubuntu / macOS 常见位置。
    """
    candidates = [
        # Alpine apk add font-wqy-microhei 安装位置（2026-07-31 新增）
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        # Alpine apk add font-noto-cjk 安装位置（备用）
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        # Alpine apk add font-dejavu 安装位置（仅 Latin）
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        # Debian/Ubuntu apt install fonts-dejavu 路径
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ]
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


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


def _image_to_pdf_bytes(
    img: Image.Image, orientation: str = "landscape",
) -> bytes:
    """把 PIL Image 渲染成单页 PDF（bytes）。

    2026-07-31 优化：去掉了原 pypdf 双重读写（PIL save PDF 后又用 PdfReader +
    PdfWriter 修 mediabox）。`PIL.Image.save(PDF, resolution=DPI)` 的 mediabox
    由 image.size / DPI * 72 推算；当 image 是精确 A4 像素尺寸（landscape
    = 1754×1240 @ 150 DPI）时 mediabox = (842, 595) pt，天然精确。
    """
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=DPI)
    return buf.getvalue()


def _detect_pdf_orientation(pdf_bytes: bytes) -> str:
    """读 PDF 第一页有效页面尺寸判断朝向。

    ⚠️ 这里的 `/Rotate` 交换是**必要**的：pikepdf 的 `page.mediabox` 是原始值，
    不含 `/Rotate`。与 `service/_print_front_cache.py` 的 pypdfium2 路径语义**相反**
    （那边 `get_size()` / `render()` 已应用 `/Rotate`，再交换就会误判朝向）。
    """
    try:
        with pikepdf.Pdf.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) == 0:
                return "landscape"
            page = pdf.pages[0]
            w_pt, h_pt = float(page.mediabox[2]), float(page.mediabox[3])
            rotation = page.get("/Rotate", 0) or 0
            if int(rotation) % 180:
                w_pt, h_pt = h_pt, w_pt
            return "landscape" if w_pt > h_pt else "portrait"
    except Exception:  # noqa: BLE001
        _logger.exception("failed to detect pdf orientation, default to landscape")
        return "landscape"


def _fit_pdf_page_to_a4(page: PageObject, orientation: str) -> None:
    """将 PDF 页面内容等比缩放并居中到精确 A4 页面。

    2026-07-31 优化：仍走 pypdf（因为 vector=1 路径仍接收任意来源 PDF，包括
    带 /Rotate 的复杂源；pikepdf 在该路径的迁移留作后续）；这部分代码仅在
    `vector=True` 旁路触发，热路径不调用。
    """
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
    drawing_sha: str | None = None
    front_pdf_bytes: bytes | None = None
    orientation: str = "landscape"
    planned_delivery_date: date | None = None
    quantity: int | None = None


async def _prepare_part_print_data(
    *,
    part_id: int,
    parts: PartRepository,
    part_files: PartFileRepository,
    vector: bool = False,
) -> _PartPrintData:
    """async 阶段：DB 查询 + COS 下载原始字节 + 正面页规格化/缓存（带 L1+L2）。

    2026-07-31 优化：正面页规格化（pypdfium2 渲染 + PIL JPEG 嵌 PDF）也搬到
    async 阶段（IO/CPU 都已在 IO 协程里）+ L1/L2 缓存查询，sync 阶段只剩
    pikepdf 合并 + 背面页矢量直出。这样同步线程无需跨协程调度，测试也无需
    构造 event loop。
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
    drawing_sha: str | None = None
    front_pdf_bytes: bytes | None = None
    front_orientation: str = "landscape"

    if master is not None:
        ext = master.file_type.upper()
        drawing_ext = ext
        drawing_sha = getattr(master, "content_sha256", None)
        try:
            if ext in {
                "PDF", "PNG", "JPG", "JPEG", "GIF", "BMP",
                "TIF", "TIFF", "WEBP", "HEIC",
            }:
                drawing_bytes = await _download_drawing_bytes(master)
                # 走规格化 + 两级缓存；失败 → front_pdf_bytes 仍为 None（上层 fallback info card）
                _front_result = await get_normalized_front_pdf(
                    original_bytes=drawing_bytes,
                    file_type=ext,
                    content_sha256=drawing_sha,
                    vector=vector,
                )
                front_pdf_bytes = _front_result.pdf_bytes
                front_orientation = _front_result.orientation
            else:
                # STEP / DWG / DXF：纸面不可直接渲染 → info card
                drawing_bytes = None
        except Exception:  # noqa: BLE001
            _logger.exception("failed to download master drawing, fallback to info card")
            drawing_bytes = None

    # 直印 part.planned_delivery_date（无 system 优先回退、无 -N 天 buffer）
    _planned = part.planned_delivery_date
    return _PartPrintData(
        part_id=part_id,
        serial_no=serial_no,
        drawing_no=part.drawing_no or "",
        name=part.name or "",
        drawing_bytes=drawing_bytes,
        drawing_ext=drawing_ext,
        orientation=front_orientation,
        planned_delivery_date=_planned if isinstance(_planned, date) else None,
        quantity=part.quantity if isinstance(getattr(part, "quantity", None), int) else None,
        drawing_sha=drawing_sha,
        front_pdf_bytes=front_pdf_bytes,
    )


def _build_part_print_pdf_sync(
    data: _PartPrintData, *, vector: bool = False,
) -> bytes:
    """sync 阶段：纯 PIL/pikepdf/ReportLab 渲染（禁止访问 ORM / session）。

    2026-07-31 优化：
    - 正面页规格化 + L1/L2 缓存由 `_prepare_part_print_data`（async）完成，
      本函数拿到的是已经处理好的 `front_pdf_bytes`；
    - 背面页由 `service/_print_back_page.py::_build_back_page_pdf` 直接生成
      单页矢量 PDF；
    - 合并换 pikepdf（C++ QPDF 后端，比 pypdf 快 10-50× 且释放 GIL）；
    - vector 参数仅影响 async 阶段的缓存策略（passthrough vs rasterize），
      本函数对 vector 不敏感。

    参数：
    - data：包含原始图纸 bytes（可选）+ 已经规格化的 front_pdf_bytes + 背面信息。
    返回：完整双面 PDF bytes
    """
    t0 = time.perf_counter()

    # ---------- 1) 正面页 bytes（async 阶段已规格化或 fallback）----------
    front_pdf_bytes = data.front_pdf_bytes
    orientation = data.orientation

    # ---------- 2) 背面页 PDF bytes（矢量，~30-50 KB）----------
    back_pdf_bytes = _build_back_page_pdf(
        data.serial_no,
        planned_delivery_date=data.planned_delivery_date,
        quantity=data.quantity,
        show_info=True,
    )

    # ---------- 3) 用 pikepdf 合并 ----------
    out_pdf = pikepdf.Pdf.new()
    # 正面（若有）；否则信息卡占位
    if front_pdf_bytes is None:
        info_card = _build_info_card_page(
            orientation=orientation,
            drawing_no=data.drawing_no,
            name=data.name,
            serial_no=data.serial_no,
            customer_path=None,
        )
        info_pdf_bytes = _image_to_pdf_bytes(info_card, orientation)
        out_pdf.pages.extend(pikepdf.Pdf.open(io.BytesIO(info_pdf_bytes)).pages)
    else:
        out_pdf.pages.extend(pikepdf.Pdf.open(io.BytesIO(front_pdf_bytes)).pages)
    # 背面（永远 landscape，矢量，边缘锐利）
    out_pdf.pages.extend(pikepdf.Pdf.open(io.BytesIO(back_pdf_bytes)).pages)

    # ---------- 4) 计时日志 ----------
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    out_buf = io.BytesIO()
    out_pdf.save(out_buf)
    out_bytes = out_buf.getvalue()
    _logger.info(
        "print_render part_id=%d serial=%s orientation=%s bytes=%d elapsed_ms=%d",
        data.part_id, data.serial_no, orientation,
        len(out_bytes), elapsed_ms,
    )
    return out_bytes


def _build_drawing_with_barcode_pages(
    *,
    drawing_bytes: bytes | None,
    serial_no: str,
    drawing_no: str,
    name: str,
    orientation: str = "landscape",
    planned_delivery_date: date | None = None,
    quantity: int | None = None,
    content_sha256: str | None = None,
    vector: bool = False,
) -> bytes:
    """组合「图纸页 + 条码背面页」双面 PDF（零件/装配体通用）；bytes 输出。

    2026-07-31：保留入口签名（外部仍调用）但内部改走 `_build_part_print_pdf_sync`
    风格的 pikepdf 合并。
    - drawing_bytes 为 None 时退化为信息卡占位。
    - 条码页强制 landscape，由 `service/_print_back_page.py` 生成矢量 PDF。
    """
    data = _PartPrintData(
        part_id=0,
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name,
        customer_path=None,
        drawing_bytes=drawing_bytes,
        drawing_ext=None,
        orientation=orientation,
        planned_delivery_date=planned_delivery_date,
        quantity=quantity,
        drawing_sha=content_sha256,
    )
    return _build_part_print_pdf_sync(data, vector=vector)


# ============================================================
# 主入口（兼容层：保持返回 bytes，内部走 _prepare + _sync）
# ============================================================
async def build_part_print_pdf(
    *,
    part_id: int,
    parts: PartRepository,
    part_files: PartFileRepository,
    vector: bool = False,
) -> bytes:
    """为指定零件生成「图纸 + 条形码」双面打印 PDF。

    朝向：自动跟随上传图纸的 orientation（landscape 优先，因图纸常用横向）；
    无图纸时默认 landscape。条码页 / 信息卡页与图纸页朝向一致，保证双面
    打印翻转方向正确。

    2026-07-10 起：图纸存储统一到 `t_part_file` (kind=DRAWING)。
    2026-07-30 起：内部拆分为 async 数据准备 + sync 纯渲染，供批量并行复用。
    2026-07-31 起：async 阶段走正面规格化 + 两级缓存；sync 阶段走背面矢量 + pikepdf 合并。
    """
    data = await _prepare_part_print_data(
        part_id=part_id, parts=parts, part_files=part_files, vector=vector,
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
    vector: bool = False,
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

    # 7. 并发下载全部图纸（Semaphore 限流；并发数从 APP_CPU_CORES 派生）
    sem = asyncio.Semaphore(settings.print_download_concurrency)

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
        if pid in drawing_metadata and drawing_metadata[pid] is not None:
            download_tasks.append(_download_with_sem(drawing_metadata[pid]))
            download_keys.append(("part", pid))

    for aid in assembly_order:
        if aid in assembly_master_metadata and assembly_master_metadata[aid] is not None:
            download_tasks.append(_download_with_sem(assembly_master_metadata[aid]))
            download_keys.append(("asm_master", aid))

    for aid in assembly_order:
        child_map = assembly_to_children.get(aid, {})
        children = sorted(child_map.values(), key=lambda c: (c.drawing_no or "", c.id))
        for c in children:
            if c.id in drawing_metadata and drawing_metadata[c.id] is not None:
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

    # 8. 构建 _PartPrintData 列表（保持打印顺序）。
    # 2026-07-31：先并行调 get_normalized_front_pdf 拿到 front_pdf_bytes，
    # 避免 sync 阶段再去桥接 asyncio。
    items_data: list[_PartPrintData] = []

    async def _prepare_one(
        pid: int | None, aid: int | None, *, asm_obj, kind: str,
    ) -> _PartPrintData | None:
        if kind == "part":
            p = all_parts_map.get(pid)
            if p is None:
                return None
            master = drawing_metadata.get(pid)
            ext = master.file_type.upper() if master else None
            sha = getattr(master, "content_sha256", None) if master else None
            raw_bytes = part_drawing_bytes.get(pid)
            front_bytes = None
            orientation = "landscape"
            if raw_bytes is not None and ext:
                try:
                    _r = await get_normalized_front_pdf(
                        original_bytes=raw_bytes, file_type=ext,
                        content_sha256=sha, vector=vector,
                    )
                    front_bytes = _r.pdf_bytes
                    orientation = _r.orientation
                except Exception:  # noqa: BLE001
                    _logger.exception("batch front normalize fail part=%d", pid)
            _planned = p.planned_delivery_date
            return _PartPrintData(
                part_id=pid, serial_no=p.serial_no or "NO-SERIAL",
                drawing_no=p.drawing_no or "", name=p.name or "",
                drawing_bytes=raw_bytes, drawing_ext=ext,
                orientation=orientation,
                planned_delivery_date=_planned if isinstance(_planned, date) else None,
                quantity=p.quantity if isinstance(getattr(p, "quantity", None), int) else None,
                drawing_sha=sha, front_pdf_bytes=front_bytes,
            )
        else:  # "asm_master"
            if asm_obj is None:
                return None
            master = assembly_master_metadata.get(aid)
            ext = master.file_type.upper() if master else None
            sha = getattr(master, "content_sha256", None) if master else None
            raw_bytes = asm_master_bytes.get(aid)
            front_bytes = None
            orientation = "landscape"
            if raw_bytes is not None and ext:
                try:
                    _r = await get_normalized_front_pdf(
                        original_bytes=raw_bytes, file_type=ext,
                        content_sha256=sha, vector=vector,
                    )
                    front_bytes = _r.pdf_bytes
                    orientation = _r.orientation
                except Exception:  # noqa: BLE001
                    _logger.exception("batch front normalize fail asm=%d", aid)
            _planned = asm_obj.planned_delivery_date
            return _PartPrintData(
                part_id=aid, serial_no=asm_obj.serial_no or "NO-SERIAL",
                drawing_no=asm_obj.drawing_no or "", name=asm_obj.name or "",
                drawing_bytes=raw_bytes, drawing_ext=ext,
                orientation=orientation,
                planned_delivery_date=_planned if isinstance(_planned, date) else None,
                quantity=(
                    asm_obj.quantity
                    if isinstance(getattr(asm_obj, "quantity", None), int)
                    else None
                ),  # 2026-08-04：总装图背面同样要打 Q:（= 装配体套数），此前漏传
                drawing_sha=sha, front_pdf_bytes=front_bytes,
            )

    for pid in ordered_part_ids:
        d = await _prepare_one(pid, None, asm_obj=None, kind="part")
        if d is not None:
            items_data.append(d)

    # 2026-08-01：装配件无总装图（ASSEMBLY_MASTER 文件不存在）时，仅跳过
    # master 页 + Code128 条码页；子件继续打印。装配件本身缺失（asm=None）
    # 同样跳过 master（防御：soft_delete / 数据库漂移兜底）。
    for aid in assembly_order:
        asm = await assemblies.get_by_id(aid) if assemblies else None
        if asm is not None and assembly_master_metadata.get(aid) is not None:
            d_asm = await _prepare_one(None, aid, asm_obj=asm, kind="asm_master")
            if d_asm is not None:
                items_data.append(d_asm)
        child_map = assembly_to_children.get(aid, {})
        children = sorted(child_map.values(), key=lambda c: (c.drawing_no or "", c.id))
        for c in children:
            d_c = await _prepare_one(c.id, None, asm_obj=None, kind="part")
            if d_c is not None:
                items_data.append(d_c)

    # 9. CPU 并行渲染（asyncio.to_thread，并发数从 APP_CPU_CORES 派生）
    render_tasks = [
        asyncio.to_thread(_build_part_print_pdf_sync, data)
        for data in items_data
    ]
    render_results = await asyncio.gather(*render_tasks, return_exceptions=True)

    # 10. 按原始顺序合并（pikepdf，比 pypdf 快 10-50× 且释放 GIL）
    out_pdf = pikepdf.Pdf.new()
    for result in render_results:
        if isinstance(result, Exception):
            logger.warning("batch print render skip: %s", result)
            continue
        try:
            out_pdf.pages.extend(pikepdf.Pdf.open(io.BytesIO(result)).pages)
        except Exception as e:  # noqa: BLE001
            logger.warning("batch print merge skip: %s", e)

    buf = io.BytesIO()
    out_pdf.save(buf)
    return buf.getvalue()
