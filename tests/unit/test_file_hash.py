"""Unit tests for core/file_hash.py.

2026-07-14 新增：
- compute_sha256_hex：基础 + 边界（空 / 大文件 / 一致性）
- safe_filename：ASCII 折叠 / 截断保留扩展名 / 空输入
- make_object_key：key 模板 / sha16 一致性 / 同内容同 key / 跨 owner 不同 key
"""
from __future__ import annotations

import hashlib

import pytest

from core.file_hash import compute_sha256_hex, make_object_key, safe_filename
from model.enums import PartFileKind


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


# ===== make_object_key =====


class TestMakeObjectKey:
    def test_part_drawing_key_layout(self):
        key = make_object_key(
            owner_id=199852260920918016,
            owner_kind="part",
            kind=PartFileKind.DRAWING,
            content_sha256="a" * 64,
            original_filename="pulley_v2.pdf",
            ext="pdf",
        )
        # 默认 cos_upload_prefix = "drawings/"
        assert key == (
            "drawings/part/199852260920918016/DRAWING/"
            "aaaaaaaaaaaaaaaa_pulley_v2.pdf"
        )

    def test_assembly_master_key_layout(self):
        key = make_object_key(
            owner_id=42,
            owner_kind="assembly",
            kind=PartFileKind.ASSEMBLY_MASTER,
            content_sha256="b" * 64,
            original_filename="master.pdf",
            ext="pdf",
        )
        assert key == (
            "drawings/assembly/42/ASSEMBLY_MASTER/"
            "bbbbbbbbbbbbbbbb_master.pdf"
        )

    def test_dedup_same_content_same_key(self):
        """同 part + 同 kind + 同 sha + 同 filename → 同 key（CAS）。"""
        kw = dict(
            owner_id=100,
            owner_kind="part",
            kind=PartFileKind.DRAWING,
            content_sha256="c" * 64,
            original_filename="foo.pdf",
            ext="pdf",
        )
        assert make_object_key(**kw) == make_object_key(**kw)

    def test_different_owner_different_key(self):
        kw = dict(
            owner_kind="part",
            kind=PartFileKind.DRAWING,
            content_sha256="d" * 64,
            original_filename="foo.pdf",
            ext="pdf",
        )
        a = make_object_key(owner_id=1, **kw)
        b = make_object_key(owner_id=2, **kw)
        assert a != b  # 跨 part 不共享 COS 对象

    def test_sha16_used_in_key(self):
        # sha16 是完整 sha 的前 16 个字符
        full = "abcdef0123456789" * 4  # 64 chars
        key = make_object_key(
            owner_id=1,
            owner_kind="part",
            kind=PartFileKind.DRAWING,
            content_sha256=full,
            original_filename="foo.pdf",
            ext="pdf",
        )
        # key 中 sha 段恰好是前 16 字符
        assert "abcdef0123456789" in key

    def test_chinese_filename_folded(self):
        key = make_object_key(
            owner_id=1,
            owner_kind="part",
            kind=PartFileKind.DRAWING,
            content_sha256="e" * 64,
            original_filename="图纸.pdf",
            ext="pdf",
        )
        # safe_filename("图纸.pdf") → "tu_zhi.pdf"（每个汉字折叠一次，
        # 中间下划线合并与否由 re.sub 决定，但断言结尾 .pdf 与 ASCII 即可）
        assert key.endswith(".pdf")
        assert "图纸" not in key  # 一定不含中文

    def test_image_ext_in_key(self):
        """PNG 图片也走新 key 模板（与 PDF 同槽）。"""
        key = make_object_key(
            owner_id=1,
            owner_kind="part",
            kind=PartFileKind.DRAWING,
            content_sha256="f" * 64,
            original_filename="photo.png",
            ext="png",
        )
        assert key.endswith(".png")
        assert "/DRAWING/" in key

    def test_cad_2d_kind_in_key(self):
        key = make_object_key(
            owner_id=1,
            owner_kind="part",
            kind=PartFileKind.CAD_2D,
            content_sha256="1" * 64,
            original_filename="source.dwg",
            ext="dwg",
        )
        assert key.endswith(".dwg")
        assert "/CAD_2D/" in key