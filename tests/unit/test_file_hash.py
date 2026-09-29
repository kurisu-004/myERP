"""Unit tests for core/file_hash.py.

2026-07-14 新增：
- compute_sha256_hex：基础 + 边界（空 / 大文件 / 一致性）
- safe_filename：ASCII 折叠 / 截断保留扩展名 / 空输入

2026-09-29 重构：删除 `make_object_key` 测试段（移至
`tests/unit/test_make_object_key.py`，覆盖新签名 `(owner_id,
content_sha256, ext)` + 新模板 `parts/{owner_id}/{sha256}.{ext}`）。
`safe_filename` 函数本身保留（本文件保留测试），但不再被
`make_object_key` 消费——`make_object_key` 已与文件名解耦。
"""

from __future__ import annotations

import hashlib

from core.file_hash import compute_sha256_hex, safe_filename

# ===== compute_sha256_hex =====


class TestComputeSha256:
    def test_empty_bytes(self):
        sha = compute_sha256_hex(b"")
        # SHA-256("") known answer
        assert sha == hashlib.sha256(b"").hexdigest()
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)

    def test_consistency(self):
        # 同一字节两次调用结果一致
        assert compute_sha256_hex(b"hello") == compute_sha256_hex(b"hello")

    def test_different_bytes_differ(self):
        a = compute_sha256_hex(b"hello")
        b = compute_sha256_hex(b"world")
        assert a != b

    def test_large_input(self):
        # 1MB 块
        data = b"x" * (1024 * 1024)
        sha = compute_sha256_hex(data)
        assert sha == hashlib.sha256(data).hexdigest()
        assert len(sha) == 64

    def test_known_answer_png(self):
        # PNG magic bytes 已知 SHA-256
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        assert compute_sha256_hex(png) == hashlib.sha256(png).hexdigest()


# ===== safe_filename =====


class TestSafeFilename:
    def test_plain_ascii_unchanged(self):
        assert safe_filename("foo.pdf") == "foo.pdf"
        assert safe_filename("bar_v2.PDF") == "bar_v2.PDF"

    def test_strips_directory(self):
        assert safe_filename("/path/to/foo.pdf") == "foo.pdf"
        assert safe_filename("a/b/c/d.svg") == "d.svg"

    def test_chinese_folded_to_underscore(self):
        # "图纸" → "_"（连续两个汉字各折叠成一个 _；这里简化为验证只
        # 剩 ASCII + .）
        result = safe_filename("图纸.pdf")
        assert all(c.isascii() and (c.isalnum() or c in "._-") for c in result)
        assert result.endswith(".pdf")

    def test_special_chars_replaced(self):
        # 空格/括号等连续折叠为 _，stem 末尾的 _ 被 strip；扩展名保留
        assert safe_filename("foo bar (v2).pdf") == "foo_bar_v2.pdf"
        assert safe_filename("a&b%c@d.step") == "a_b_c_d.step"

    def test_truncates_long_names_preserving_ext(self):
        long = "a" * 200 + ".pdf"
        out = safe_filename(long, max_len=80)
        assert len(out) <= 80
        assert out.endswith(".pdf")

    def test_truncates_without_ext(self):
        long = "a" * 200
        out = safe_filename(long, max_len=80)
        assert len(out) == 80
        assert "." not in out

    def test_empty_input_fallback(self):
        assert safe_filename("") == "file"
        assert safe_filename("///") == "file"
        # "...___" rpartition → stem="", ext="___"（3 字符，视为合法扩展名保留）
        assert safe_filename("...___") == "file.___"
        # 真没有 dot 时整段 strip
        assert safe_filename("___") == "file"

    def test_strips_leading_trailing_dots_dashes(self):
        assert safe_filename("...foo...") == "foo"
        assert safe_filename("___bar___") == "bar"
        assert safe_filename("---baz---") == "baz"
