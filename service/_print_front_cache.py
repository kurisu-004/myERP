"""打印正面页：原图纸 → A4 单页 PDF 规格化 + 两级缓存。

2026-07-31 打印性能优化引入。3Mbps 带宽下，CAD 矢量 PDF 原样 passthrough 是
最大的传输瓶颈（单件 2-5MB → 纯传输 6-15s）。本模块：

1. 规格化：把上传的图纸一次性转成 200DPI 单页 A4 JPEG PDF（~300KB）。
   - PDF 输入：pypdfium2 渲染第一页 → PIL JPEG q85 → PIL.save(PDF, resolution=200)；
   - 图片输入：ImageOps.contain + draft() 解码降采样 → 同上。
   - PIL.save(PDF, resolution=200) 的 mediabox 天然精确 A4，无需 pypdf 二次修。

2. 两级缓存（key = content_sha256，CAS 不可变，永不失效）：
   - L1 本地磁盘 LRU：`/app/.cache/print/{sha}.pdf`，1GB 上限；
   - L2 COS：`printcache/{sha}.pdf`，跨容器重建持久。
   - L1 miss → 查 L2 → 命中回填 L1；都 miss → 下载原图 → 规格化 → 写 L1
     + asyncio.create_task 回传 L2（fire-and-forget，沿用 part_file.py 模式）。

3. 逃生门：`vector=True` 参数跳过规格化，走原有 passthrough 路径（保留
   原 _fit_pdf_page_to_a4 行为；个别图纸打不清晰时用）。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from core import cos as cos_mod
from core.config import settings
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

# 本地磁盘缓存配置
_CACHE_DIR = Path(os.environ.get("PRINT_CACHE_DIR", "/app/.cache/print"))
_CACHE_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
# COS 远端缓存 key 前缀
_L2_KEY_PREFIX = "printcache/"

# 图片输入白名单（service/part_file.py 的 ALLOWED_EXTS_BY_KIND[DRAWING] 同步子集）
_IMAGE_EXTS = {"PNG", "JPG", "JPEG", "GIF", "BMP", "TIF", "TIFF", "WEBP"}


@dataclass
class FrontCacheResult:
    """正面页规格化结果：bytes + 朝向 + 源标记（用于日志）。"""

    pdf_bytes: bytes
    orientation: str  # "landscape" | "portrait"
    source: str       # "l1" | "l2" | "render"


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
# L1 本地磁盘 LRU
# ============================================================
def _l1_path(sha: str) -> Path:
    return _CACHE_DIR / f"{sha}.pdf"


def _l1_get(sha: str) -> bytes | None:
    p = _l1_path(sha)
    try:
        if p.exists() and p.stat().st_size > 0:
            return p.read_bytes()
    except OSError as e:
        _logger.warning("L1 cache read failed for %s: %s", sha, e)
    return None


def _l1_put(sha: str, pdf_bytes: bytes) -> None:
    """写本地磁盘；LRU 上限 1GB，超出按 mtime 淘汰最旧。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # 临时写再 rename，避免并发读到半成品
        tmp = _l1_path(sha).with_suffix(".pdf.tmp")
        tmp.write_bytes(pdf_bytes)
        tmp.replace(_l1_path(sha))
    except OSError as e:
        _logger.warning("L1 cache write failed for %s: %s", sha, e)
        return
    _l1_evict_if_over()


def _l1_evict_if_over() -> None:
    """LRU 淘汰：按 mtime asc 删到 ≤ 上限。"""
    try:
        files = [(p, p.stat().st_mtime) for p in _CACHE_DIR.glob("*.pdf")]
    except OSError as e:
        _logger.warning("L1 cache stat failed: %s", e)
        return
    total = sum(p.stat().st_size for p, _ in files)
    files.sort(key=lambda t: t[1])  # 最旧在前
    while total > _CACHE_MAX_BYTES and files:
        p, _ = files.pop(0)
        try:
            total -= p.stat().st_size
            p.unlink()
        except OSError:
            pass


# ============================================================
# L2 COS 远端缓存
# ============================================================
def _l2_key(sha: str) -> str:
    return f"{_L2_KEY_PREFIX}{sha}.pdf"


async def _l2_get(sha: str) -> bytes | None:
    """下载 COS 上的 printcache/{sha}.pdf；不存在返回 None。"""
    try:
        if await cos_mod.head_object(_l2_key(sha)) is None:
            return None
        return await cos_mod.download_object(_l2_key(sha))
    except BizError as e:
        _logger.warning("L2 cache read failed for %s: %s", sha, e)
        return None
    except Exception as e:  # noqa: BLE001
        _logger.warning("L2 cache read unexpected for %s: %s", sha, e)
        return None


async def _l2_put_async(sha: str, pdf_bytes: bytes) -> None:
    """异步回传 COS；失败仅 warning（fire-and-forget）。"""
    try:
        await cos_mod.upload_object(_l2_key(sha), pdf_bytes, "application/pdf")
    except Exception as e:  # noqa: BLE001
        _logger.warning("L2 cache upload failed for %s: %s", sha, e)


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

    - `vector=True`：跳过规格化 + 缓存，直接返回原 PDF bytes + 朝向
      （由 service/printing.py 走 passthrough + _fit_pdf_page_to_a4）。
    - `vector=False`（默认）：走两级缓存；cache key = content_sha256。
    """
    t0 = time.perf_counter()

    # ---- vector=1 逃生门：原样 passthrough ----
    if vector:
        if file_type.upper() == "PDF":
            size = _detect_pdf_size(original_bytes)
            orientation = "landscape" if (size and size[0] > size[1]) else "landscape"
            return FrontCacheResult(
                pdf_bytes=original_bytes, orientation=orientation, source="vector",
            )
        # 图片走一遍规格化（避免 pikepdf 合并报错）
        pdf_bytes, orientation = _render_image_to_a4_jpeg_pdf(original_bytes)
        return FrontCacheResult(
            pdf_bytes=pdf_bytes, orientation=orientation, source="vector_image",
        )

    # ---- 无 sha 兜底：直接规格化（不缓存）----
    # 仅在 sha 是合法字符串时才走两级缓存；None / MagicMock / 其他类型都视为无 sha
    # （测试场景可能传入 Mock 对象作为占位）。
    sha = content_sha256 if isinstance(content_sha256, str) and content_sha256 else None
    if not sha:
        return await _render_and_return(original_bytes, file_type, source="render_no_sha")

    # ---- L1 → L2 → render ----
    cached = _l1_get(sha)
    if cached is not None:
        # 朝向：从缓存 PDF 的 mediabox 推算（landscape / portrait）。
        try:
            with pikepdf.Pdf.open(io.BytesIO(cached)) as _pdf:
                _w, _h = float(_pdf.pages[0].mediabox[2]), float(_pdf.pages[0].mediabox[3])
            cached_orientation = "landscape" if _w > _h else "portrait"
        except Exception:  # noqa: BLE001
            cached_orientation = "landscape"
        return FrontCacheResult(
            pdf_bytes=cached, orientation=cached_orientation, source="l1",
        )

    l2_bytes = await _l2_get(sha)
    if l2_bytes is not None:
        _l1_put(sha, l2_bytes)
        # L2 命中同样从 mediabox 推朝向
        try:
            with pikepdf.Pdf.open(io.BytesIO(l2_bytes)) as _pdf:
                _w, _h = float(_pdf.pages[0].mediabox[2]), float(_pdf.pages[0].mediabox[3])
            cached_orientation = "landscape" if _w > _h else "portrait"
        except Exception:  # noqa: BLE001
            cached_orientation = "landscape"
        return FrontCacheResult(
            pdf_bytes=l2_bytes, orientation=cached_orientation, source="l2",
        )

    # ---- 都 miss：规格化 → 写 L1 → 异步写 L2 ----
    result = await _render_and_return(original_bytes, file_type, source="render")
    _l1_put(sha, result.pdf_bytes)
    # 异步回传 L2（fire-and-forget；4GB 内存下不阻塞当前请求）
    asyncio.create_task(_l2_put_async(sha, result.pdf_bytes))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _logger.info(
        "front cache render: sha=%s... bytes=%d elapsed_ms=%d",
        sha[:16], len(result.pdf_bytes), elapsed_ms,
    )
    return result


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


# ============================================================
# 启动期目录准备（main.py lifespan 调用）
# ============================================================
def ensure_cache_dir() -> None:
    """容器启动时建好 L1 缓存目录（Dockerfile 已 mkdir，重复调用 idempotent）。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _logger.warning("ensure cache dir failed: %s", e)