"""PDF 工具（纯函数，无 DB / 无 COS 依赖，便于单测）。"""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter


def count_pages(pdf_bytes: bytes) -> int:
    """返回 PDF 页数。损坏 / 加密的 PDF 会抛 pypdf 异常。"""
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def split_pdf(pdf_bytes: bytes) -> list[bytes]:
    """把多页 PDF 拆成 N 个单页 PDF 字节流；index 0 = page 1。

    每个结果都是合法的单页 PDF（可被 PdfReader 解析、len(pages) == 1）。
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    out: list[bytes] = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out
