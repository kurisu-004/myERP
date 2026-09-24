"""2026-09-24 重构：MCP 域整体下线，本仓 service package 仅剩 STS 凭证端口
所需的薄层 service。

活跃 service：
- `StsService`  — `api/v1/sts.py`（STS 临时凭证端口）

历史 service（mcp / part_file / dashboard 等）整体迁至 `_archive/service/`
或已删除，业务由 backend-rust v2 的 `/api/v2/*` 承接。

工具 / 内部助手：service/_*.py 全留。
"""

from .sts import StsService

__all__ = ["StsService"]
