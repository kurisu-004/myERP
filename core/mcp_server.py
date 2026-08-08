"""FastMCP 服务构建（2026-08-08 新增）。

把 `/api/mcp/*` 下的只读 REST 端点转成 MCP tools，对外挂在 `/mcp`
（Streamable HTTP）。AI / MCP host 连 `http://<host>:8000/mcp` 即可。

为什么走 `from_fastapi` 而不是手写 `@mcp.tool`：
端点本来就要以 REST 形式存在（供非 MCP 调用方用），`from_fastapi` 让
`response_model` 直接变成 MCP `outputSchema`、docstring 直接变成 tool description，
一份代码两种用法，不用手维护两套。

依赖说明：`fastmcp` pin 了 `mcp>=1.24,<2.0`。官方 SDK 2.0 是破坏性重写，
`fastmcp` 尚未跟进——升级 `mcp` 前先确认 `fastmcp` 放开了这个上界。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

# ⚠️⚠️ 这三条规则**缺一不可，且顺序有意义**（先匹配先生效）。
#
# fastmcp 内部会在你给的规则**后面**自动追加
# `DEFAULT_ROUTE_MAPPINGS = [RouteMap(mcp_type=MCPType.TOOL)]`——一条「其余全变 TOOL」
# 的兜底规则。所以没有第 3 条终止 EXCLUDE 的话，这就不是白名单：本项目 150 个
# 业务端点会**全部**变成免鉴权的 MCP tool。改动这个列表时务必重跑
# `tests/test_mcp_api.py::test_only_whitelisted_endpoints_become_mcp_tools`。
_ROUTE_MAPS = [
    # 1. 图纸内容端点返回二进制，做成 tool 没有意义（MCP 只能塞文本），
    #    它只作为 HTTP 链接供 host 自己 GET。必须排在规则 2 前面。
    RouteMap(pattern=r"^/api/mcp/files/", mcp_type=MCPType.EXCLUDE),
    # 2. 白名单：只有 /api/mcp/ 下的 GET 端点成为 tool。
    RouteMap(pattern=r"^/api/mcp/", methods=["GET"], mcp_type=MCPType.TOOL),
    # 3. 终止规则：其余一律排除，顶掉 fastmcp 的兜底 TOOL 规则。
    RouteMap(mcp_type=MCPType.EXCLUDE),
]


def build_mcp_app(app: FastAPI):
    """从已注册完路由的 FastAPI app 构建 MCP 的 ASGI 子应用。

    ⚠️ 必须在**所有** `include_router` / 路由装饰器执行完之后调用：
    `from_fastapi` 会在构造时立刻 `app.openapi()` 快照一次，之后新增的路由不可见。

    返回的对象自带 lifespan（用来起 streamable-HTTP session manager）。
    Starlette **不会**自动跑被 mount 的子 app 的 lifespan，调用方必须把它合并进
    主 app 的 lifespan——见 `main.py::_combined_lifespan`。
    """
    mcp = FastMCP.from_fastapi(
        app=app,
        name="myERP",
        route_maps=_ROUTE_MAPS,
    )
    # path="/"：mount 到 "/mcp" 时前缀已经由 mount 提供，
    # 这里再用默认的 "/mcp" 会变成 /mcp/mcp。
    return mcp.http_app(path="/")
