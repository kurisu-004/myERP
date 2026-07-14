"""文件哈希 + 安全文件名 + COS 对象 key 派生。

2026-07-14 起新加：

- `compute_sha256_hex(data) -> str`：SHA-256 hex（64 字符），
  用于 `t_part_file.content_sha256` 与去重部分唯一索引
  `uk_t_part_file_part_kind_sha`。
- `safe_filename(name, max_len=80) -> str`：ASCII 折叠文件名，
  防止中文 / 特殊字符在 COS key / URL 编码上出问题；
  DB `original_filename` 仍保留完整 UTF-8 给 UI 显示。
- `make_object_key(...) -> str`：基于 owner / kind / sha16 / safe_filename 派生
  COS key，模板：

      {prefix}{owner_kind}/{owner_id}/{KIND}/{sha16}_{safe_filename}.{ext}

  从桶扫描该 key 即可知 owner（part/assembly）、kind、short content hash、
  原始文件名，便于 DB 丢失时人工恢复。
"""
from __future__ import annotations

import hashlib
import re
from typing import Literal

from core.config import settings
from model.enums import PartFileKind

# 只保留 ASCII 字母数字 + . _ - ；其余字符折叠成 _。
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def compute_sha256_hex(data: bytes) -> str:
    """计算 SHA-256 并返回 64 字符小写 hex 字符串。

    注：100MB 文件约 150-200 ms（单线程，CPython hashlib）。
    后续如需更高吞吐可改 streaming `hashlib.sha256().update(chunk)`。
    """
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str, max_len: int = 80) -> str:
    """把文件名折叠成 ASCII 安全字符串（最长 `max_len` 字符，**保留扩展名**）。

    规则：
    1. 取最后一段（去目录）
    2. 非 `[A-Za-z0-9._-]` 字符替换为 `_`
    3. 识别扩展名（最后一段 `.xxx`，xxx 长度 1-7）：
       - 仅 strip stem 段两端的 `._-`，保留 `.xxx`
       - 无扩展名或扩展名过长：整段 strip
    4. 超过 `max_len` 时优先保留扩展名截断

    示例：
        "图纸.pdf"             -> "__.pdf"（每个汉字折叠成单个 _）
        "主轴 (v2).STEP"       -> "__v2_.STEP"
        "/path/to/foo bar.PDF" -> "foo_bar.PDF"
        ""                     -> "file"
        "...foo"               -> "foo"（无扩展名，整段 strip）
        "a" * 200 + ".pdf"     -> "aaaa...aaa.pdf"（截断到 80 字符保留扩展名）
    """
    base = name.rsplit("/", 1)[-1]
    folded = _FILENAME_SAFE_RE.sub("_", base)

    # 分离 stem 与 ext（ext 长度 1..7 视为合法扩展名）
    stem, dot, ext = folded.rpartition(".")
    if dot and 0 < len(ext) < 8:
        stem = stem.strip("._-") or "file"
        result = f"{stem}.{ext}"
    else:
        result = folded.strip("._-") or "file"

    if len(result) > max_len:
        stem2, dot2, ext2 = result.rpartition(".")
        if dot2 and 0 < len(ext2) < 8:
            stem2 = stem2[: max_len - len(ext2) - 1]
            result = f"{stem2}.{ext2}"
        else:
            result = result[:max_len]
    return result


# owner_kind 必须是字面量 "part" 或 "assembly"，用来拼路径段。
OwnerKind = Literal["part", "assembly"]


def make_object_key(
    *,
    owner_id: int,
    owner_kind: OwnerKind,
    kind: PartFileKind,
    content_sha256: str,
    original_filename: str,
    ext: str,
) -> str:
    """派生 COS 对象 key。

    模板：
        {prefix}{owner_kind}/{owner_id}/{KIND}/{sha16}_{safe_filename}

    注：safe_filename 已**包含扩展名**（如 `foo.pdf` / `__.pdf`），这里
    不再追加 `.{ext}`。`ext` 参数保留是用于 caller 显式记录（目前
    `service/part_file.py` 通过它生成 content_type / file_type，与 key 派生
    解耦）。

    - `sha16` = SHA-256 前 16 hex 字符（用于人眼识别；DB 存完整 64 字符做
      精确去重）
    - `safe_filename` = `safe_filename(original_filename)`，ASCII 折叠 + 保留扩展名
    """
    sha16 = content_sha256[:16]
    safe = safe_filename(original_filename)
    prefix = settings.cos_upload_prefix
    return f"{prefix}{owner_kind}/{owner_id}/{kind.value}/{sha16}_{safe}"