"""2026-09-17 重构：v1 业务路由下线 + JWT bypass 后只保留仍被 api/mcp/
或 auth 流引用的 service。

活跃 service：
- `AuthService`      — `api/v1/auth.py` (login/refresh)
- `UserService`      — `api/v1/auth.py` (change-password)
- `build_menu_tree`  — `AuthService.login/refresh/me` 构造菜单
- `McpQueryService`  — `api/mcp/parts.py` (AI 只读)
- `PartFileService`  — `api/mcp/files.py` (文件代理)
- `build_snapshot_with_workers` — dashboard WS（`api.v1.ws` 仍懒加载）
- `StsService`       — `api/v1/sts.py` (新增)

2026-09-17 STS 端口 PR：auto_complete_loop 已删除（由 backend-rust v2
task/auto_complete.rs 接管），本仓 lifespan 不再 spawn。

工具 / 内部助手：service/_*.py 全留；具体业务 service（如 applicant/
assembly/part/delivery_note/...）整体移至 `_archive/service/`，由
backend-rust v2 承接。
"""
from .auth import AuthService
from .dashboard import build_snapshot_with_workers
from .menu import build_menu_tree
from .part_file import PartFileService

from .mcp_query import McpQueryService
from .sts import StsService
from .user import UserService

__all__ = [
    "AuthService",
    "McpQueryService",
    "PartFileService",
    "StsService",
    "UserService",
    "build_menu_tree",
    "build_snapshot_with_workers",
]