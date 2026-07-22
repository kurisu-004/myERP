from fastapi import FastAPI
import uvicorn

from core.config import settings
from core.database import lifespan
from core.exception_handler import register_exception_handlers
from core.middleware import RequestSizeLimitMiddleware, UnifiedResponseMiddleware
from api import api_router

app = FastAPI(lifespan=lifespan)

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
