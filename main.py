from fastapi import FastAPI
import uvicorn

from core.database import lifespan
from core.exception_handler import register_exception_handlers
from core.middleware import UnifiedResponseMiddleware
from api import api_router

app = FastAPI(lifespan=lifespan)
app.add_middleware(UnifiedResponseMiddleware)
register_exception_handlers(app)
app.include_router(api_router)


@app.get("/api/v1/health")
async def health():
    """容器健康检查端点；不查 DB（DB 联通由 lifespan 心跳保证）。"""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
