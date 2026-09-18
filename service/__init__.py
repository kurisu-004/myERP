"""2026-09-19 重构：python 端 IAM 域（auth/user/menu）整体迁出至 backend-rust v2。

活跃 service：
- `McpQueryService`  — `api/mcp/parts.py` (AI 只读)
- `PartFileService`  — `api/mcp/files.py` (文件代理)
- `build_snapshot_with_workers` — dashboard WS（`api.v1.ws` 仍懒加载）
- `StsService`       — `api/v1/sts.py` (STS 临时凭证端口)

2026-09-17 STS 端口 PR：auto_complete_loop 已删除（由 backend-rust v2
task/auto_complete.rs 接管），本仓 lifespan 不再 spawn。

工具 / 内部助手：service/_*.py 全留；具体业务 service（applicant/
assembly/auth/user/menu/part/delivery_note/...）整体移至 `_archive/service/`，
由 backend-rust v2 承接。
"""

from .dashboard import build_snapshot_with_workers
from .mcp_query import McpQueryService
from .part_file import PartFileService
from .sts import StsService

__all__ = [
    "McpQueryService",
    "PartFileService",
    "StsService",
    "build_snapshot_with_workers",
]
