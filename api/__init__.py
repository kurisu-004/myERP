"""API 路由聚合。

2026-09-24：MCP 域（`/api/mcp/*`）整体下线后，本聚合器仅挂 v1（3 个 STS 端口）
+ `/api/v1/health`。MCP AI 只读入口由业务自行调用 backend-rust v2 端点取代。

历史聚合过的 `/api/mcp/*` 路由已删除（详见 `chore(mcp): 删除整个 MCP 域` 提交）。
"""

from fastapi import APIRouter

from . import v1

api_router = APIRouter(prefix="/api")
api_router.include_router(v1.api_router)
