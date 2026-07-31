"""Unit tests for service/_print_front_cache.py pure normalize + vector escape.

2026-07-31 引入与重写：覆盖
- PDF 输入 → A4 JPEG PDF + 朝向（landscape / portrait 各一）；
- 图片输入（PNG）→ A4 JPEG PDF；
- vector=True 跳过规格化直接 passthrough（PDF + 图片）；
- 无 content_sha256 仍能正常渲染（不抛异常）；
- source 字段值与预期匹配（render / render_no_sha / vector / vector_image）。

无 DB / 无 COS 依赖：纯函数 + 内存 bytes IO。
"""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from service import _print_front_cache as fc


# ============================================================
# 工具：构造最小测试 PDF / PNG
# ============================================================
def _fake_pdf(width_pt: float = 842.0, height_pt: float = 595.0) -> bytes:
    """构造一页指定点尺寸的空白 PDF（landscape 842×595 / portrait 595×842）。"""
    writer = PdfWriter()
    writer.add_blank_page(width=width_pt, height=height_pt)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _fake_png(width: int = 800, height: int = 600) -> bytes:
    """构造一张单色 PNG（landscape 800×600）。"""
    img = Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _assert_is_valid_pdf(pdf_bytes: bytes) -> None:
    """健全性检查：bytes 能被 pypdf 解析，至少 1 页；页面尺寸接近 A4。"""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1
    page = reader.pages[0]
    w_pt = float(page.mediabox[2] - page.mediabox[0])
    h_pt = float(page.mediabox[3] - page.mediabox[1])
    # 规格化目标 A4：portrait 595×842 / landscape 842×595。
    # PIL PDF driver 会做 mm/pt 归一化，归一化后实测 ≈ 841.89 × 595.27（容差 1.0）。
    is_landscape = abs(w_pt - 842.0) < 1.0 and abs(h_pt - 595.0) < 1.0
    is_portrait = abs(w_pt - 595.0) < 1.0 and abs(h_pt - 842.0) < 1.0
    assert is_landscape or is_portrait, (
        f"mediabox=({w_pt}, {h_pt}) not within A4 tolerance"
    )


# ============================================================
# PDF 输入
# ============================================================
class TestPdfNormalize:
    @pytest.mark.asyncio
    async def test_landscape_pdf_yields_landscape_a4(self):
        """横向 PDF（842×595 pt）→ 规格化后 orientation=landscape。"""
        src = _fake_pdf(width_pt=842.0, height_pt=595.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256="a" * 64,
        )
        assert result.orientation == "landscape"
        assert result.source == "render"
        _assert_is_valid_pdf(result.pdf_bytes)

    @pytest.mark.asyncio
    async def test_portrait_pdf_yields_portrait_a4(self):
        """纵向 PDF（595×842 pt）→ 规格化后 orientation=portrait。"""
        src = _fake_pdf(width_pt=595.0, height_pt=842.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256="b" * 64,
        )
        assert result.orientation == "portrait"
        assert result.source == "render"
        _assert_is_valid_pdf(result.pdf_bytes)


# ============================================================
# 图片输入
# ============================================================
class TestImageNormalize:
    @pytest.mark.asyncio
    async def test_png_landscape_yields_landscape_a4(self):
        """landscape PNG（800×600）→ 规格化后 orientation=landscape。"""
        src = _fake_png(width=800, height=600)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PNG", content_sha256="c" * 64,
        )
        assert result.orientation == "landscape"
        assert result.source == "render"
        _assert_is_valid_pdf(result.pdf_bytes)

    @pytest.mark.asyncio
    async def test_png_portrait_yields_portrait_a4(self):
        """portrait PNG（600×800）→ 规格化后 orientation=portrait。"""
        src = _fake_png(width=600, height=800)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PNG", content_sha256="d" * 64,
        )
        assert result.orientation == "portrait"
        assert result.source == "render"
        _assert_is_valid_pdf(result.pdf_bytes)


# ============================================================
# vector 逃生门
# ============================================================
class TestVectorEscape:
    @pytest.mark.asyncio
    async def test_vector_pdf_passthrough(self):
        """vector=True + PDF 输入：原样 passthrough，source=vector。"""
        src = _fake_pdf(width_pt=842.0, height_pt=595.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256="e" * 64,
            vector=True,
        )
        assert result.source == "vector"
        assert result.pdf_bytes == src  # 原样 passthrough
        assert result.orientation == "landscape"

    @pytest.mark.asyncio
    async def test_vector_portrait_pdf_passthrough(self):
        """vector=True + portrait PDF：原样 passthrough，orientation=portrait。"""
        src = _fake_pdf(width_pt=595.0, height_pt=842.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256="f" * 64,
            vector=True,
        )
        assert result.source == "vector"
        assert result.pdf_bytes == src
        assert result.orientation == "portrait"

    @pytest.mark.asyncio
    async def test_vector_image_still_normalizes(self):
        """vector=True + 图片输入：仍走规格化（避免 pikepdf 合并报错），source=vector_image。"""
        src = _fake_png(width=800, height=600)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PNG", content_sha256="0" * 64,
            vector=True,
        )
        assert result.source == "vector_image"
        # 与 passthrough 不同：结果应是规格化后的 A4 PDF
        _assert_is_valid_pdf(result.pdf_bytes)
        assert result.orientation == "landscape"


# ============================================================
# 无 content_sha256 兜底
# ============================================================
class TestNoShaFallback:
    @pytest.mark.asyncio
    async def test_no_sha_pdf_renders_with_render_no_sha(self):
        """content_sha256=None：仍能正常渲染，source=render_no_sha。"""
        src = _fake_pdf(width_pt=842.0, height_pt=595.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256=None,
        )
        assert result.source == "render_no_sha"
        assert result.orientation == "landscape"
        _assert_is_valid_pdf(result.pdf_bytes)

    @pytest.mark.asyncio
    async def test_empty_sha_string_renders_with_render_no_sha(self):
        """content_sha256=""：视为无 sha，走 source=render_no_sha。"""
        src = _fake_pdf(width_pt=842.0, height_pt=595.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256="",
        )
        assert result.source == "render_no_sha"

    @pytest.mark.asyncio
    async def test_non_string_sha_treated_as_no_sha(self):
        """content_sha256 非字符串（如测试场景传 Mock）→ render_no_sha，不抛异常。"""
        src = _fake_pdf(width_pt=842.0, height_pt=595.0)
        result = await fc.get_normalized_front_pdf(
            original_bytes=src, file_type="PDF", content_sha256=12345,
        )
        assert result.source == "render_no_sha"


# ============================================================
# FrontCacheResult 字段
# ============================================================
class TestFrontCacheResult:
    def test_field_signature_preserved(self):
        """FrontCacheResult 字段保持完全一致（service/printing.py 依赖）。"""
        r = fc.FrontCacheResult(pdf_bytes=b"x", orientation="landscape", source="render")
        assert hasattr(r, "pdf_bytes")
        assert hasattr(r, "orientation")
        assert hasattr(r, "source")
        assert r.pdf_bytes == b"x"
        assert r.orientation == "landscape"
        assert r.source == "render"
