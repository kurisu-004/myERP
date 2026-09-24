"""myERP FastAPI 应用入口。

2026-09-24：MCP 域（`/api/mcp/*` + `/mcp` mount）整体下线后，本仓仅保留 STS
凭证签发 + 健康检查（标签 / 送货单打印端点新增见后续 PR）。

路由现状：
- `POST /api/v1/files/sts-tmp-keys`
- `POST /api/v1/files/sts-prefix-credentials`
- `GET  /api/v1/files/sts-health`
- `GET  /api/v1/health`（容器健康检查）

鉴权：所有 STS 端口裸开（`api/v1/sts.py`），靠部署层 nginx / 安全组隔离保证；
本仓不持有 IAM 抽象（已迁至 backend-rust v2 的 `/api/v2/iam/*`）。
"""

import uvicorn
from fastapi import FastAPI

from api import api_router
from core.config import settings
from core.database import lifespan as db_lifespan
from core.exception_handler import register_exception_handlers
from core.middleware import RequestSizeLimitMiddleware, UnifiedResponseMiddleware

app = FastAPI(lifespan=db_lifespan)

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


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
