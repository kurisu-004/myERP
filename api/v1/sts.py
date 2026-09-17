"""2026-09-17 新增：STS 临时凭证端口（前端直传 COS 用）。

裸开鉴权（参考 `/api/mcp/*` 模式，2026-09-17 起 v1 业务路由已 JWT bypass，
此端点更不依赖 `get_current_user`），靠部署层 nginx / 安全组隔离。

路径：`POST /api/v1/files/sts-tmp-keys`
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_sts_service
from schema.sts import StsTmpKeysRequest, StsTmpKeysResponse
from service.sts import StsService


router = APIRouter(prefix="/files", tags=["sts"])


@router.post(
    "/sts-tmp-keys",
    response_model=StsTmpKeysResponse,
    operation_id="grant_sts_tmp_keys",
    summary="前端直传 COS 临时凭证端口",
)
async def grant_sts_tmp_keys(
    body: StsTmpKeysRequest,
    svc: StsService = Depends(get_sts_service),
) -> StsTmpKeysResponse:
    return await svc.grant_tmp_keys(body)