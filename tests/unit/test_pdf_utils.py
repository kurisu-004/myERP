"""Unit tests for utils/pdf.py (count_pages / split_pdf).

纯函数测试，零 DB / 零 COS 依赖。
"""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from utils.pdf import count_pages, split_pdf


def _fake_pdf(n: int) -> bytes:
    """构造 N 页空白 PDF（不依赖外部 fixture）。"""
    writer = PdfWriter()
    for _ in range(n):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ============================================================
# count_pages
# ============================================================


class TestCountPages:
    def test_zero_pages(self) -> None:
        # 0 页 PDF 极少但合法（一些损坏/空文件可能产生）
        # PdfWriter 允许 0 页；count_pages 应返回 0
        assert count_pages(_fake_pdf(0)) == 0

    def test_one_page(self) -> None:
        assert count_pages(_fake_pdf(1)) == 1

    def test_three_pages(self) -> None:
        assert count_pages(_fake_pdf(3)) == 3

    def test_corrupt_pdf_raises(self) -> None:
        with pytest.raises(Exception):
            count_pages(b"this is not a pdf")

    def test_empty_bytes_raises(self) -> None:
        with pytest.raises(Exception):
            count_pages(b"")


# ============================================================
# split_pdf
# ============================================================


class TestSplitPdf:
    def test_zero_pages(self) -> None:
        assert split_pdf(_fake_pdf(0)) == []

    def test_one_page(self) -> None:
        splits = split_pdf(_fake_pdf(1))
        assert len(splits) == 1
        # 每片都是合法的单页 PDF
        assert count_pages(splits[0]) == 1

    def test_three_pages_each_is_single(self) -> None:
        splits = split_pdf(_fake_pdf(3))
        assert len(splits) == 3
        for i, blob in enumerate(splits, start=1):
            assert count_pages(blob) == 1, f"split[{i - 1}] should be 1 page"

    def test_split_preserves_page_count(self) -> None:
        original = _fake_pdf(5)
        splits = split_pdf(original)
        assert len(splits) == 5
        assert count_pages(original) == 5

    def test_split_corrupt_raises(self) -> None:
        with pytest.raises(Exception):
            split_pdf(b"not a pdf")

    def test_each_split_is_valid_pdf(self) -> None:
        """确保每个 split blob 都能被 PdfReader 解析（不只 count_pages 通过）。"""
        splits = split_pdf(_fake_pdf(3))
        for blob in splits:
            reader = PdfReader(io.BytesIO(blob))
            # 至少 1 页
            assert len(reader.pages) >= 1
            # 页面有 mediabox（健全性）
            page = reader.pages[0]
            assert page.mediabox.width > 0
            assert page.mediabox.height > 0
