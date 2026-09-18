"""2026-09-17 新增：STS 临时凭证端口（前端直传 COS 用）。

裸开鉴权（参考 `/api/mcp/*` 模式，2026-09-17 起 v1 业务路由已 JWT bypass，
此端点更不依赖 `get_current_user`），靠部署层 nginx / 安全组隔离。

路径（2026-09-18 review：列入 CLAUDE.md §14「保留端点」段）：
- `POST /api/v1/files/sts-tmp-keys`            — 前端直传 COS（`tmp/<uid>/<sha16>/*` 命名空间）。
- `POST /api/v1/files/sts-prefix-credentials`  — 2026-09-18 新增：内部端口——
  供 rust 后端按任意 `tmp/...` 前缀签凭证，不返回 `tmp_key`。
- `GET  /api/v1/files/sts-health`              — 2026-09-18 新增：STS 签发自检
  （healthcheck 探针）——真实调一次 SDK 签发，验证整条链路（SDK + 主账号密钥
  + CAM policy + 到 sts.tencentcloudapi.com 的网络）；HTTP 200 表示通过，
  BizError 透传（自带 http_status）让 compose healthcheck 拿到非 2xx 即
  fail。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_sts_service
from schema.sts import (
    StsHealthResponse,
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


# 2026-09-18 新增：STS 签发自检（healthcheck 探针）。裸开鉴权，与上面两个
# sts 端点保持一致（参考 /api/mcp/* 模式；安全性靠部署层 nginx / 安全组隔离）。
# 真实调一次 SDK 签发（不 mock）以验证 SDK + 主账号密钥 + CAM policy + 网络
# 联通整条链路；BizError 透传，compose healthcheck 拿 HTTP 状态判定 ok/fail。
@router.get(
    "/sts-health",
    response_model=StsHealthResponse,
    operation_id="sts_health_check",
    summary="STS 签发自检（healthcheck 探针）",
)
async def sts_health_check(
    svc: StsService = Depends(get_sts_service),
) -> StsHealthResponse:
    """2026-09-18 新增：自检端点（healthcheck 探针）。

    每次调用生成 uuid4 hex probe prefix `tmp/__sts_healthcheck__/<hex>/probe`
    （命名空间隔离 + 同一秒多次探活不会撞 CAM policy 收口），以 60s TTL 真签
    一次 STS 凭证，验证整条链路：
    1. qcloud-python-sts SDK 可调用
    2. 主账号 secret_id / secret_key 正确
    3. CAM policy 模板可被 SDK 序列化为有效 policy
    4. 出口网络到 sts.tencentcloudapi.com 可达

    不 PutObject / 不写 DB / 不写 Redis——零数据变更副作用。失败由 BizError
    透传（自带 http_status=502 等），compose healthcheck 拿到非 2xx 即 fail。
    """
    return await svc.sts_health()
