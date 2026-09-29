"""文件哈希 + 安全文件名 + COS 对象 key 派生。

2026-07-14 起新加：

- `compute_sha256_hex(data) -> str`：SHA-256 hex（64 字符），
  用于 `t_part_file.content_sha256` 与去重部分唯一索引
  `uk_t_part_file_part_kind_sha`。
- `safe_filename(name, max_len=80) -> str`：ASCII 折叠文件名，
  防止中文 / 特殊字符在 COS key / URL 编码上出问题；
  DB `original_filename` 仍保留完整 UTF-8 给 UI 显示。

  2026-09-29 重构：保留函数但**不再**被 `make_object_key` / STS 临时
  key 派生调用（两端新模板均不再需要 ASCII 折叠文件名段——后端不做
  filename 推断，前端显式传 `ext`）。保留供未来可能复用 / 历史调用方；
  当前无活跃 production 调用方。

- `make_object_key(owner_id, content_sha256, ext) -> str`：2026-09-29
  重构：COS 对象 key 模板由三层 / 五段（`{prefix}{owner_kind}/
  {owner_id}/{KIND}/{sha16}_{safe_filename}`）改为两层
  `parts/{owner_id}/{sha256}.{ext}`：

  - 去掉 `cos_upload_prefix`（如 `drawings/`）：桶内 COS key 不再
    携带业务前缀，分域靠 prefix-virtual-host（endpoint 层面）；
  - 去掉 `owner_kind` 段（`part` / `assembly`）：拆桶靠
    `owner_id` 的雪花 ID 实例号（v1 / v2 后端不同 instance）隔离，
    DB 表 `t_part_file` polymorphic part_id 仍由 application 层
    解码；
  - 去掉 `KIND` 段：part_file 表的 `kind` 与 COS 对象 key 解耦，靠
    `t_part_file.id` 关联回查；
  - 去掉 `safe_filename` 段：filename 由 DB `t_part_file.original_filename`
    单独存，COS key 不再含可读文件名（中文 / 路径穿越 / URL 编码问题
    一并消失）；
  - `sha16` → `sha256`（64 hex 完整）：人眼识别成本由 filename 段
    转移给 DB，前端 UI 走 `original_filename` 显示；key 完整哈希段
    提供从桶扫描 → DB 反查所需的全部信息。

  调用方（如未来的 backend-rust 服务化）需自行保证 `ext` 是合法小写
  字母数字 1..7 字符（与 STS `StsTmpKeysRequest.ext` schema 同款约束）。
"""

from __future__ import annotations

import hashlib
import re

# 只保留 ASCII 字母数字 + . _ - ；其余字符折叠成 _。
# 2026-09-29 重构：保留此常量供 `safe_filename` 使用；`make_object_key`
# 不再消费。
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def compute_sha256_hex(data: bytes) -> str:
    """计算 SHA-256 并返回 64 字符小写 hex 字符串。

    注：100MB 文件约 150-200 ms（单线程，CPython hashlib）。
    后续如需更高吞吐可改 streaming `hashlib.sha256().update(chunk)`。
    """
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str, max_len: int = 80) -> str:
    """把文件名折叠成 ASCII 安全字符串（最长 `max_len` 字符，**保留扩展名**）。

    2026-09-29 重构：函数保留供 `core/_file_kind_policy.py` 等其他模块
    复用；本函数**不**再被 `make_object_key` 消费（COS key 模板去掉了
    safe_filename 段）。docstring 维持原描述。

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


def make_object_key(
    *,
    owner_id: int,
    content_sha256: str,
    ext: str,
) -> str:
    """派生 COS 对象 key（2026-09-29 重构）。

    模板：
        parts/{owner_id}/{sha256}.{ext}

    - `content_sha256` 必须为 64 字符小写 hex（与 `compute_sha256_hex`
      输出一致；本函数不做校验，调用方负责）。
    - `ext` 必须为小写字母数字 1..7 字符（与 STS `StsTmpKeysRequest.ext`
      schema 同款约束；本函数不做校验，调用方负责）。
    - `owner_id` 是雪花 ID；part_file 表 polymorphic part_id 拆桶由
      application 层 `owner_kind` 解析，COS key 不再携带 owner_kind 段。

    注：本函数目前无 backend-python 内部 caller，仅作为跨语言约定镜像
    存在；真正在生产跑的是 backend-rust 的 `build_cas_key`。

    设计取舍：
    - 不带 `cos_upload_prefix`：桶内 key 由 `parts/{owner_id}/` 段直接
      锁定；跨桶 / 跨环境的 prefix 切换靠 endpoint / bucket 切换，不靠
      key prefix。
    - 不带 `safe_filename`：原文件名由 DB `t_part_file.original_filename`
      单独存，UI 显示走 DB；COS key 不再受中文 / 路径穿越 / URL 编码
      问题困扰。
    - 不带 `KIND` 段：t_part_file.kind 与 COS key 解耦，靠
      `t_part_file.id` 反查。
    """
    return f"parts/{owner_id}/{content_sha256}.{ext}"
