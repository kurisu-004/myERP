"""打印正面页：原图纸 → A4 单页 PDF 规格化 + vector 逃生门。

2026-07-31 打印性能优化引入。3Mbps 带宽下，CAD 矢量 PDF 原样 passthrough 是
最大的传输瓶颈（单件 2-5MB → 纯传输 6-15s）。本模块负责：

1. 规格化：把上传的图纸一次性转成 200DPI 单页 A4 JPEG PDF（~300KB）。
   - PDF 输入：pypdfium2 渲染第一页 → PIL JPEG q85 → PIL.save(PDF, resolution=200)；
   - 图片输入：ImageOps.contain + draft() 解码降采样 → 同上。
   - PIL.save(PDF, resolution=200) 的 mediabox 天然精确 A4，无需 pypdf 二次修。

2. 逃生门：`vector=True` 参数跳过规格化，走原有 passthrough 路径（保留
   原 _fit_pdf_page_to_a4 行为；个别图纸打不清晰时用）。

历史备注：早期版本曾引入 L1 本地磁盘 + L2 COS 两级缓存，因图纸基本只打印
一次（命中率低）+ COS 规格化结果相对原 PDF 体积优势有限（仍需存储/请求费用）
于 2026-07-31 移除；本模块现在只负责规格化本体与 vector 逃生门。
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from core.exception import BizError

_logger = logging.getLogger(__name__)

# 规格化目标参数
_TARGET_DPI = 200
_JPEG_QUALITY = 85
# A4 像素尺寸（pt * DPI/72）
_A4_W_PT = 595.0
_A4_H_PT = 842.0
_A4_W_PX = int(_A4_W_PT * _TARGET_DPI / 72.0)  # 1654
_A4_H_PX = int(_A4_H_PT * _TARGET_DPI / 72.0)  # 2339
_A4_W_PX_LAND = _A4_H_PX                        # 2339
_A4_H_PX_LAND = _A4_W_PX                        # 1654

# 图片输入白名单（service/part_file.py 的 ALLOWED_EXTS_BY_KIND[DRAWING] 同步子集）
_IMAGE_EXTS = {"PNG", "JPG", "JPEG", "GIF", "BMP", "TIF", "TIFF", "WEBP"}


@dataclass
class FrontCacheResult:
    """正面页规格化结果：bytes + 朝向 + 源标记（用于日志）。

    `source` 取值：
    - "render"：PDF / 图片按规格化走完，命中 content_sha256；
    - "render_no_sha"：规格化走完，但 content_sha256 不可用（不缓存）；
    - "vector"：vector=True + PDF 输入，原样 passthrough；
    - "vector_image"：vector=True + 图片输入，仍走规格化（避免 pikepdf 合并报错）。
    """

    pdf_bytes: bytes
    orientation: str  # "landscape" | "portrait"
    source: str       # "render" | "render_no_sha" | "vector" | "vector_image"


# ============================================================
# 朝向探测 + 规格化
# ============================================================
def _detect_pdf_size(pdf_bytes: bytes) -> tuple[int, int] | None:
    """读 PDF 第一页可视尺寸（处理 rotation），返回 (w_pt, h_pt)。失败返回 None。"""
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
        if len(pdf) == 0:
            return None
        page = pdf[0]
        w, h = page.get_size()
        rotation = page.get_rotation()
        if rotation % 180:
            w, h = h, w
        return int(w), int(h)
    except Exception as e:  # noqa: BLE001
        _logger.warning("pypdfium2 detect size failed: %s", e)
        return None


def _render_pdf_page_to_a4_jpeg_pdf(pdf_bytes: bytes) -> tuple[bytes, str]:
    """把 PDF 第一页光栅化为 A4 JPEG PDF，返回 (pdf_bytes, orientation)。

    流程：pypdfium2 渲染源页到 PIL Image（按 _TARGET_DPI 缩放） → 居中贴到
    精确 A4 像素画布 → JPEG 编码 → PIL.save(PDF, resolution=_TARGET_DPI)，
    PIL 的 mediabox 推算 = px / DPI * 72，对 A4 像素画布来说就是 (595,842)
    或 (842,595) pt，天然精确。
    """
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    if len(pdf) == 0:
        raise BizError  # 实际由调用方 fallback 到 info card

    page = pdf[0]
    rotation = page.get_rotation()
    w_pt, h_pt = page.get_size()
    if rotation % 180:
        w_pt, h_pt = h_pt, w_pt
    orientation = "landscape" if w_pt > h_pt else "portrait"

    # pypdfium2 render：用 scale = DPI / 72 让输出像素 ≈ A4 像素
    scale = _TARGET_DPI / 72.0
    pil = page.render(scale=scale).to_pil().convert("RGB")
    pdf.close()

    return _pil_to_a4_pdf(pil, orientation), orientation


def _pil_to_a4_pdf(pil: Image.Image, orientation: str) -> bytes:
    """把任意 PIL Image 缩放并居中贴到精确 A4 像素画布，再 save PDF。

    PIL.save(PDF, resolution=DPI) 的 mediabox = image.size / DPI * 72。
    我们把画布做成精确 A4 像素尺寸（portrait: 1654×2339 / landscape: 2339×1654），
    mediabox 自然就是 595×842 或 842×595 pt，无需后续修正。
    """
    canvas_w = _A4_W_PX_LAND if orientation == "landscape" else _A4_W_PX
    canvas_h = _A4_H_PX_LAND if orientation == "landscape" else _A4_H_PX
    # ImageOps.contain 等比缩放到不超过画布
    pil = ImageOps.contain(pil, (canvas_w, canvas_h))
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
    off_x = (canvas_w - pil.width) // 2
    off_y = (canvas_h - pil.height) // 2
    canvas.paste(pil, (off_x, off_y))
    buf = io.BytesIO()
    canvas.save(buf, "PDF", resolution=_TARGET_DPI, quality=_JPEG_QUALITY)
    return buf.getvalue()


def _render_image_to_a4_jpeg_pdf(image_bytes: bytes) -> tuple[bytes, str]:
    """图片 → A4 JPEG PDF；JPEG 走 draft() 提前降采样。"""
    img = Image.open(io.BytesIO(image_bytes))
    # JPEG/Huge TIFF 解码降采样（4-8× 加速）
    if hasattr(img, "draft") and img.format in {"JPEG", "TIFF", "WEBP"}:
        try:
            # 目标像素 = 画布最大边；让 draft 选最接近的 1/2/4/8 缩放档
            target_max = max(_A4_W_PX, _A4_W_PX_LAND)
            cur_max = max(img.size)
            factor = max(1, cur_max // target_max)
            if factor > 1:
                img.draft(None, (max(1, img.width // factor), max(1, img.height // factor)))
        except Exception:  # noqa: BLE001
            pass
    img = img.convert("RGB")
    orientation = "landscape" if img.width > img.height else "portrait"
    return _pil_to_a4_pdf(img, orientation), orientation


# ============================================================
# 主入口
# ============================================================
async def get_normalized_front_pdf(
    *,
    original_bytes: bytes,
    file_type: str,
    content_sha256: str | None,
    vector: bool = False,
) -> FrontCacheResult:
    """为单件图纸返回 A4 单页 PDF bytes + 朝向。

    - `vector=True`：跳过规格化（PDF 输入原样 passthrough；图片输入仍走规格化
      避免 pikepdf 合并报错），直接返回 front pdf bytes + 朝向
      （由 service/printing.py 走 passthrough + _fit_pdf_page_to_a4）。
    - `vector=False`（默认）：按 file_type 走规格化（PDF 第一页 / 图片 → A4 JPEG PDF）；
      不再缓存。
    """
    # ---- vector=1 逃生门：原样 passthrough ----
    if vector:
        if file_type.upper() == "PDF":
            size = _detect_pdf_size(original_bytes)
            orientation = "landscape" if (size and size[0] > size[1]) else "portrait"
            return FrontCacheResult(
                pdf_bytes=original_bytes, orientation=orientation, source="vector",
            )
        # 图片走一遍规格化（避免 pikepdf 合并报错）
        pdf_bytes, orientation = _render_image_to_a4_jpeg_pdf(original_bytes)
        return FrontCacheResult(
            pdf_bytes=pdf_bytes, orientation=orientation, source="vector_image",
        )

    # ---- 无 sha 兜底：直接规格化（不缓存）----
    # 仅在 sha 是合法字符串时才标记为 "render"；None / MagicMock / 其他类型
    # 视为无 sha，按 "render_no_sha" 走规格化（测试场景可能传入 Mock 对象占位）。
    sha = content_sha256 if isinstance(content_sha256, str) and content_sha256 else None
    if not sha:
        return await _render_and_return(original_bytes, file_type, source="render_no_sha")
    return await _render_and_return(original_bytes, file_type, source="render")


async def _render_and_return(
    original_bytes: bytes, file_type: str, *, source: str,
) -> FrontCacheResult:
    ext = file_type.upper()
    if ext == "PDF":
        pdf_bytes, orientation = _render_pdf_page_to_a4_jpeg_pdf(original_bytes)
    elif ext in _IMAGE_EXTS:
        pdf_bytes, orientation = _render_image_to_a4_jpeg_pdf(original_bytes)
    else:
        # STEP/DWG/DXF 等不可直渲染：上层会 fallback 到 info card
        raise ValueError(f"unsupported file_type for normalize: {ext}")
    return FrontCacheResult(pdf_bytes=pdf_bytes, orientation=orientation, source=source)
