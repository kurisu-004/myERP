"""2026-09-17 新增：STS 临时凭证端口（前端直传 COS 用）。

裸开鉴权（参考 `/api/mcp/*` 模式，2026-09-17 起 v1 业务路由已 JWT bypass，
此端点更不依赖 `get_current_user`），靠部署层 nginx / 安全组隔离。

路径（2026-09-18 review：列入 CLAUDE.md §14「保留端点」段）：
- `POST /api/v1/files/sts-tmp-keys`            — 前端直传 COS（`tmp/<uid>/<sha16>/*` 命名空间）。
- `POST /api/v1/files/sts-prefix-credentials`  — 2026-09-18 新增：内部端口——
  供 rust 后端按任意 `tmp/...` 前缀签凭证，不返回 `tmp_key`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_sts_service
from schema.sts import (
    StsPrefixCredentialsRequest,
    StsPrefixCredentialsResponse,
    StsTmpKeysRequest,
    StsTmpKeysResponse,
)
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


@router.post(
    "/sts-prefix-credentials",
    response_model=StsPrefixCredentialsResponse,
    operation_id="grant_sts_prefix_credentials",
    summary="内部端口——供 rust 后端调用：按前缀签 STS 临时凭证",
)
async def grant_sts_prefix_credentials(
    body: StsPrefixCredentialsRequest,
    svc: StsService = Depends(get_sts_service),
) -> StsPrefixCredentialsResponse:
    """2026-09-18 新增：按调用方传入的 prefix 签 STS 临时凭证。

    - `prefix` 必须以 `tmp/` 开头，否则返回 `BIZ_STS_PREFIX_INVALID`。
    - duration 在 service 层 clamp 到 `settings.sts_max_ttl_seconds`。
    - 响应不包含 `tmp_key`（rust 后端自行拼对象 key）。

    裸开鉴权：安全性靠 nginx `/api/v1/files/` 不暴露 / 安全组隔离保证。
    """
    return await svc.grant_prefix_credentials(body)
