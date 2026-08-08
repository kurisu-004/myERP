"""MCP 只读查询 API（2026-08-08 新增）。

`/api/mcp/*` 是给 AI（MCP host）用的免鉴权只读入口，与 `/api/v1/*` 完全隔离：

| | `/api/v1/*` | `/api/mcp/*` |
|---|---|---|
| 鉴权 | JWT + 角色 | 无（靠 nginx / 安全组隔离） |
| 响应信封 | `{code, message, data}` | 2xx 裸 JSON；4xx/5xx 仍是信封 |
| 写操作 | 有 | **禁止**，只读 |
| 消费者 | 前端 / 扫码台 | AI |

⚠️ 信封只在 **2xx** 上被豁免（`core/middleware.py::_NO_WRAP_PREFIXES`）。
错误响应仍由 `core/exception_handler.py` 统一返回 `{code, message, data}` —— 那是
全局兜底，不为 MCP 单开一套错误契约。这不影响 MCP：tool 的 `outputSchema` 只描述
成功返回值，非 2xx 会被 fastmcp 转成协议层的 tool error，AI 读到的是 `message` 文本。

这些端点会被 `core/mcp_server.py` 用 `route_maps` 白名单转成 MCP tools。
新增端点时记得给显式 `operation_id`（= tool 名）和 `response_model`（= outputSchema），
并同步更新 `tests/test_mcp_api.py` 里写死的 tool 名集合。
"""
from fastapi import APIRouter

from api.mcp import files, parts

mcp_router = APIRouter(prefix="/mcp", tags=["MCP"])
mcp_router.include_router(parts.router)
mcp_router.include_router(files.router)

__all__ = ["mcp_router"]
