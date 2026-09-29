"""2026-09-17 新增：STS 临时凭证端口（前端直传 COS 用）。

裸开鉴权（参考 `/api/mcp/*` 模式，2026-09-17 起 v1 业务路由已 JWT bypass，
此端点更不依赖 `get_current_user`），靠部署层 nginx / 安全组隔离。

**2026-09-29 依赖部署层 rust 转发层注入 `X-Forwarded-User-Id`**；本路
由直接信任此 header 是来自 rust 的（非外部用户），因为前端的 STS 请求
都走 rust（baseURL `/api/v2`，详见 frontend nginx 反代配置 + backend-rust
docs/api/files.md）：浏览器不直接命中本端点，rust `/api/v2/files/sts-tmp-keys`
转发到本端点时把 JWT 解出的 `user_id` 写进 header，本路由读后用于拼
`tmp/{uid}/{sha256}.{ext}` 的 `{uid}` 段；缺失回退
`settings.sts_default_user_id`。

路径（2026-09-28 review：列入 CLAUDE.md §14「保留端点」段）：
- `POST /api/v1/files/sts-tmp-keys`            — 前端直传 COS（`tmp/<uid>/<sha256>.<ext>` 命名空间）。
  2026-09-28 扩展为 Union 入参：接受单文件 schema
  （`{purpose, filename, content_type, expire_seconds, content_sha256}`）
  或批量 schema（`{scope, files[1..200]}`），由 Pydantic v2 smart union
  自动区分。响应也分别为 `StsTmpKeysResponse` 或
  `StsBatchTmpKeysResponse`。链路 A 单文件场景向后兼容。
- `GET  /api/v1/files/sts-health`              — 2026-09-18 新增：STS 签发自检
  （healthcheck 探针）——真实调一次 SDK 签发，验证整条链路（SDK + 主账号密钥
  + CAM policy + 到 sts.tencentcloudapi.com 的网络）；HTTP 200 表示通过，
  BizError 透传（自带 http_status）让 compose healthcheck 拿到非 2xx 即
  fail。

2026-09-28 删除 `POST /api/v1/files/sts-prefix-credentials`：rust 后端
upload_session 域下线后已无调用方（plan
`sts-session-uploader-sts-sts-sequential-globe` §2.2）。
2026-09-28 review 第 1 轮修复：把 `assert isinstance(body, ...)` 改为
显式 `if/else` 分支——`assert` 在 CPython `-O` / `PYTHONOPTIMIZE=1`
下被 strip，边界 schema（如 `{}` / `{"purpose": "drawing"}` /
`{"scope": "x"}`）若 Pydantic v2 smart union 误分类，会绕过 assert
直接走 service，缺字段抛 AttributeError / KeyError → 500。改为
显式分支 + 兜底 BizError 400 是健壮性硬要求。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from api.deps import get_sts_service
from core.error_code import ErrCode
from core.exception import BizError
from schema.sts import (
    StsBatchTmpKeysRequest,
    StsHealthResponse,
    StsTmpKeysEndpointBody,
    StsTmpKeysEndpointResponse,
    StsTmpKeysRequest,
)
from service.sts import StsService

router = APIRouter(prefix="/files", tags=["sts"])


@router.post(
    "/sts-tmp-keys",
    response_model=StsTmpKeysEndpointResponse,
    operation_id="grant_sts_tmp_keys",
    summary="前端直传 COS 临时凭证端口（支持单文件 / 批量）",
)
async def grant_sts_tmp_keys(
    body: StsTmpKeysEndpointBody,
    # 2026-09-29 新增：依赖部署层 rust 转发层注入 `X-Forwarded-User-Id`。
    # FastAPI 自动按 `int` 注解把 header 字符串解析为 int；非法值（如
    # `"abc"`）自动抛 422 ValidationError。
    x_user_id: int | None = Header(
        default=None,
        alias="X-Forwarded-User-Id",
        description=(
            "rust 转发层从 JWT 解出的 user_id；缺失回退 "
            "`settings.sts_default_user_id`（详见 service 层 docstring）。"
        ),
    ),
    svc: StsService = Depends(get_sts_service),
) -> StsTmpKeysEndpointResponse:
    """2026-09-28 扩展：接受单文件或批量 schema。

    - 单文件 schema (`{purpose, filename, ...}`) → 走
      `service.grant_tmp_keys`，向后兼容旧链路 A；
    - 批量 schema (`{scope, files[1..200]}`) → 走
      `service.grant_tmp_keys_batch`，并发签名批。

    Pydantic v2 smart union 按字段形态自动区分；不显式 tag 字段。

    2026-09-29 新增：`x_user_id`（来自 `X-Forwarded-User-Id` header）
    透传给 service 层——用于拼 tmp_key 的 `{user_id}` 段；缺失回退
    `settings.sts_default_user_id`。
    """
    # 2026-09-28 review 第 1 轮修复：显式 if/else 分流（不依赖 `assert`
    # 或运行时类型守卫的隐式行为）。理论上 Pydantic 已按 smart union
    # 把 `body` 限定为 `StsTmpKeysRequest | StsBatchTmpKeysRequest` 之
    # 一——这里再硬限一次，防御性兜底。
    if isinstance(body, StsBatchTmpKeysRequest):
        return await svc.grant_tmp_keys_batch(body, x_user_id=x_user_id)
    if isinstance(body, StsTmpKeysRequest):
        return await svc.grant_tmp_keys(body, x_user_id=x_user_id)
    raise BizError(
        code=ErrCode.BIZ_INVALID_VALUE,
        message="unrecognized body shape",
        http_status=400,
    )


# 2026-09-18 新增：STS 签发自检（healthcheck 探针）。裸开鉴权，与上面
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
