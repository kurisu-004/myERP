"""Unit tests for `core.file_hash.make_object_key` —— 2026-09-29 重构版。

覆盖新签名 `(owner_id, content_sha256, ext)` + 新模板
`parts/{owner_id}/{sha256}.{ext}`：

- 三层模板（旧 `{prefix}{owner_kind}/{owner_id}/{KIND}/{sha16}_{safe_filename}`）
  折成两层 `parts/{owner_id}/{sha256}.{ext}`：去 prefix / owner_kind /
  KIND / safe_filename 段；
- sha16 截断 → 完整 sha256（64 hex）；
- ext 由前端显式传，**不**做 filename 推断。

验收覆盖（按本文件用例序号对应验收清单 §13）：
1. 新模板形态正确（含完整 sha256 + ext 后缀、无 prefix/owner_kind/KIND/filename 段）；
2. owner_kind / PartFileKind 参数已删除（**keyword-only** 必填三参）；
3. dedup 同 owner + 同 sha + 同 ext → 同 key（CAS）；
4. 跨 owner 不同 key；
5. 完整 sha256 进 key（不再截 sha16）；
6. filename 不影响 key（与原文件名完全解耦）；
7. PNG / DWG 等其它 ext 同样支持；
8. 函数对 content_sha256 长度 / ext 合法性**不**做校验——属调用方职责。
"""

from __future__ import annotations

import hashlib

import pytest

from core.file_hash import make_object_key

# 2026-09-29 重构：测试用统一 sha fixture。
SHA_64_A = "a" * 64
SHA_64_B = "b" * 64
SHA_64_C = "c" * 64


class TestMakeObjectKey:
    """2026-09-29 重构：新签名 `(owner_id, content_sha256, ext)` +
    新模板 `parts/{owner_id}/{sha256}.{ext}`。"""

    def test_part_owner_basic_layout(self) -> None:
        """新模板形态：`parts/{owner_id}/{sha256}.{ext}`，无 prefix /
        owner_kind / KIND / safe_filename 段。"""
        key = make_object_key(
            owner_id=199852260920918016,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        assert key == f"parts/199852260920918016/{SHA_64_A}.pdf"

    def test_assembly_owner_basic_layout(self) -> None:
        """不同 owner_id → 不同 key（owner_id 是雪花 ID）。"""
        key = make_object_key(
            owner_id=42,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        assert key == f"parts/42/{SHA_64_A}.pdf"

    def test_dedup_same_content_same_key(self) -> None:
        """同 owner + 同 sha + 同 ext → 同 key（CAS）。"""
        kw = {
            "owner_id": 100,
            "content_sha256": SHA_64_A,
            "ext": "pdf",
        }
        assert make_object_key(**kw) == make_object_key(**kw)

    def test_different_owner_different_key(self) -> None:
        """跨 owner 不同 key（owner_id 是 key 唯一性的一部分）。"""
        kw = {
            "content_sha256": SHA_64_A,
            "ext": "pdf",
        }
        a = make_object_key(owner_id=1, **kw)
        b = make_object_key(owner_id=2, **kw)
        assert a != b

    def test_different_sha_different_key(self) -> None:
        """同 owner + 不同 sha → 不同 key。"""
        kw = {"owner_id": 1, "ext": "pdf"}
        a = make_object_key(content_sha256=SHA_64_A, **kw)
        b = make_object_key(content_sha256=SHA_64_B, **kw)
        assert a != b

    def test_different_ext_different_key(self) -> None:
        """同 owner + 同 sha + 不同 ext → 不同 key（CAS 允许多 ext）。"""
        kw = {"owner_id": 1, "content_sha256": SHA_64_A}
        pdf = make_object_key(ext="pdf", **kw)
        png = make_object_key(ext="png", **kw)
        assert pdf != png
        assert pdf == f"parts/1/{SHA_64_A}.pdf"
        assert png == f"parts/1/{SHA_64_A}.png"

    def test_full_sha256_in_key(self) -> None:
        """完整 64 hex sha256 进 key（不再是 sha16 截断）。"""
        full = "abcdef0123456789" * 4  # 64 chars
        key = make_object_key(
            owner_id=1,
            content_sha256=full,
            ext="pdf",
        )
        # 完整 sha256 段出现
        assert full in key
        # sha16 段（"abcdef0123456789"）自然也在（子串），但关键断言是
        # 末尾 48 hex（"abcdef0123456789" * 3）也在 key 中。
        assert full[16:] in key

    def test_filename_does_not_affect_key(self) -> None:
        """2026-09-29 重构：原文件名完全不影响 key（无 safe_filename 段）。
        同 owner + 同 sha + 同 ext 时，filename 任意改 → key 不变。"""
        kw = {"owner_id": 1, "content_sha256": SHA_64_A, "ext": "pdf"}
        # 本函数不接受 filename 参数；通过相同 kw 反复调，模拟「原文件
        # 名不同」的语义——key 必须相同。
        assert make_object_key(**kw) == make_object_key(**kw)
        # 关键不变量：key 不含任何常见扩展名段以外的字符串
        key = make_object_key(**kw)
        assert key == f"parts/1/{SHA_64_A}.pdf"
        # 不含中文字符（safe_filename 段已删除，不会再有）
        assert "图纸" not in key
        assert ".pdf" in key  # 仅 ext 段

    def test_chinese_filename_does_not_leak_into_key(self) -> None:
        """2026-09-29 重构：原 filename 是中文也不影响 key（即便函数
        不接受 filename 参数，本断言仍是金标准——任何字符串拼接逻辑
        都不应让中文进入 key）。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        assert "图纸" not in key
        assert key == f"parts/1/{SHA_64_A}.pdf"

    def test_png_ext_supported(self) -> None:
        """PNG 等图片格式也走新模板。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="png",
        )
        assert key == f"parts/1/{SHA_64_A}.png"
        assert key.endswith(".png")

    def test_dwg_ext_supported(self) -> None:
        """CAD 源文件（dwg / dxf）也走新模板。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="dwg",
        )
        assert key == f"parts/1/{SHA_64_A}.dwg"
        assert key.endswith(".dwg")

    def test_step_ext_supported(self) -> None:
        """3D 模型格式（step / iges / stl）也走新模板。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="step",
        )
        assert key == f"parts/1/{SHA_64_A}.step"
        assert key.endswith(".step")

    def test_no_upload_prefix_in_key(self) -> None:
        """2026-09-29 重构：去掉了 `cos_upload_prefix` 段（如
        `drawings/`）。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        assert not key.startswith("drawings/")
        assert not key.startswith("/drawings/")

    def test_no_owner_kind_in_key(self) -> None:
        """2026-09-29 重构：去掉了 `owner_kind` 段（`part` / `assembly`）。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        # owner_kind 不再出现在 key 中
        assert "/part/" not in key
        assert "/assembly/" not in key

    def test_no_kind_segment_in_key(self) -> None:
        """2026-09-29 重构：去掉了 `KIND` 段（`DRAWING` / `ASSEMBLY_MASTER`
        等不再出现在 key 中）。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        assert "/DRAWING/" not in key
        assert "/ASSEMBLY_MASTER/" not in key
        assert "/CAD_2D/" not in key
        assert "/3D_MODEL/" not in key
        assert "/G_CODE/" not in key
        assert "/SETUP_SHEET/" not in key

    def test_known_sha256_value(self) -> None:
        """用真实 SHA-256（空字节）派生 key——与 `compute_sha256_hex`
        输出对齐。"""
        sha = hashlib.sha256(b"").hexdigest()
        key = make_object_key(
            owner_id=1,
            content_sha256=sha,
            ext="pdf",
        )
        assert key == f"parts/1/{sha}.pdf"

    def test_no_validation_on_content_sha256(self) -> None:
        """2026-09-29 重构：函数对 `content_sha256` 长度 / 合法性**不**
        做校验——属调用方职责。本测试验证：非 64 hex 也能成功调用（拼
        出的 key 不保证是合法 COS key）。"""
        # 不抛异常
        key = make_object_key(
            owner_id=1,
            content_sha256="nohash",
            ext="pdf",
        )
        assert key == "parts/1/nohash.pdf"

    def test_no_validation_on_ext(self) -> None:
        """2026-09-29 重构：函数对 `ext` 合法性**不**做校验——属调用方
        职责。"""
        # 大写 ext 也能调用（schema 层负责校验）
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="PDF",
        )
        assert key == f"parts/1/{SHA_64_A}.PDF"
        # 含 `.` 的 ext 也能调用
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="p.df",
        )
        assert key == f"parts/1/{SHA_64_A}.p.df"

    def test_keyword_only_signature(self) -> None:
        """2026-09-29 重构：函数三个必填参数都是 keyword-only（`*`
        强制）——确保未来加参数不会破坏位置参数契约。"""
        with pytest.raises(TypeError):
            # 位置参数调用必须失败
            make_object_key(1, SHA_64_A, "pdf")  # type: ignore[misc]

    def test_owner_id_zero_yields_valid_key(self) -> None:
        """owner_id = 0 → `parts/0/{sha}.{ext}`（虽然 owner_id=0 业务
        上无意义，但函数本身不做校验）。"""
        key = make_object_key(
            owner_id=0,
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        assert key == f"parts/0/{SHA_64_A}.pdf"

    def test_ext_empty_yields_dot_suffix(self) -> None:
        """ext="" 时 key 末尾是 `.`（双重 dot）——本函数不做 ext 校验，
        调用方负责确保 ext 非空。"""
        key = make_object_key(
            owner_id=1,
            content_sha256=SHA_64_A,
            ext="",
        )
        assert key == f"parts/1/{SHA_64_A}."

    def test_key_template_components(self) -> None:
        """key 必须由固定 3 段拼成：`parts/` / `{owner_id}/` /
        `{sha256}.{ext}`。"""
        key = make_object_key(
            owner_id=42,
            content_sha256=SHA_64_B,
            ext="png",
        )
        # 拆段验证
        assert key.startswith("parts/")
        # `parts/{owner_id}/{sha256}.{ext}` = 3 段（按 `/` 切）
        parts = key.split("/")
        assert parts == ["parts", "42", f"{SHA_64_B}.png"]
        # 最后一段按 `.` 切必须是 [sha256, ext] 两段
        last = parts[-1]
        assert last.split(".") == [SHA_64_B, "png"]
