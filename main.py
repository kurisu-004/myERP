from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from core.config import settings
from core.database import lifespan as db_lifespan
from core.exception_handler import register_exception_handlers
from core.mcp_server import build_mcp_app
from core.middleware import RequestSizeLimitMiddleware, UnifiedResponseMiddleware
from api import api_router

# MCP 的 ASGI 子应用。在文件末尾（全部路由注册完之后）才被赋值——见下面 mount 处。
# `_combined_lifespan` 只在服务启动时才读它，那会儿模块早已执行完，所以延迟赋值安全。
_mcp_app = None


@asynccontextmanager
async def _combined_lifespan(app: FastAPI):
    """业务 lifespan + MCP session manager lifespan。

    `mcp.http_app()` 自带 lifespan 用来启动 streamable-HTTP 的 session manager，
    而 Starlette **不会**自动运行被 `mount` 的子 app 的 lifespan——不在这里手动
    合并的话，第一次 MCP 调用就会因为 session manager 没启动而报错。
    """
    async with db_lifespan(app):
        if _mcp_app is None:  # 理论上不会发生，兜底避免启动直接崩
            yield
        else:
            async with _mcp_app.lifespan(app):
                yield


app = FastAPI(lifespan=_combined_lifespan)

# middleware 注册顺序 = 外→内（FastAPI 官方约定）。
# RequestSizeLimitMiddleware 放外层：在 Starlette MultiPartParser 解析 multipart 之前
# 先按 Content-Length 拦截 413，避免批量 PDF (300 MB) 全量读进内存导致 OOM。
# UnifiedResponseMiddleware 放内层：仅对放行的请求做信封包装。
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_bytes=settings.max_request_body_size_bytes,
)
app.add_middleware(UnifiedResponseMiddleware)
register_exception_handlers(app)
app.include_router(api_router)


@app.get("/api/v1/health")
async def health():
    """容器健康检查端点；不查 DB（DB 联通由 lifespan 心跳保证）。"""
    return {"status": "ok"}


# ⚠️ 必须放在所有路由注册之后：build_mcp_app 会立刻对 app.openapi() 拍快照，
# 此后新增的路由不会出现在 MCP tool 列表里。
_mcp_app = build_mcp_app(app)
# `/mcp` 不在 UnifiedResponseMiddleware 的 _WRAP_PREFIXES 里，
# 所以 MCP 协议响应天然不会被信封包装。
app.mount("/mcp", _mcp_app)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
