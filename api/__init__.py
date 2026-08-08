from fastapi import APIRouter

from . import v1
from .mcp import mcp_router

api_router = APIRouter(prefix="/api")
api_router.include_router(v1.api_router)
# 2026-08-08：AI 专用只读入口 → /api/mcp/*。免鉴权，不走响应信封。
api_router.include_router(mcp_router)
