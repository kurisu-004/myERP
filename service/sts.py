"""2026-09-17 新增：STS 临时凭证 service（薄层）。

只做参数整理 + 拼 `tmp_key` + 注入 endpoint / scheme / 上传前缀等响应
字段；不持有 session / 不写 DB（无状态）。BIZError 由 `core.sts` 直接抛。

2026-09-18 新增 `grant_prefix_credentials`：内部端口（供 rust 后端按
前缀签凭证），仅做 prefix 校验（`tmp/` 开头）+ duration clamp，policy
签发统一走 `core.sts.grant_credentials_for_prefix`。

2026-09-18 新增 `sts_health`：healthcheck 探针端口（compose 探活用），
真实调一次 `grant_credentials_for_prefix`，返回 `{status, probe_prefix,
expired_at}`。BizError 由 core 层直接透传（自带 http_status，让
healthcheck 拿到非 2xx 即 fail）。

2026-09-28 删除 `grant_prefix_credentials`：rust 后端 upload_session
域下线后无调用方（plan
`sts-session-uploader-sts-sts-sequential-globe` §2.2）。
2026-09-28 新增 `grant_tmp_keys_batch`：扩展
`POST /api/v1/files/sts-tmp-keys` 接受 `files[]` 数组入参，内部用
`asyncio.gather` 并发调 `core.sts.grant_credentials_for_prefix`（每文件
一次签名，共享 bucket/region/scheme / endpoint 等独立字段）。单文件入
口 `grant_tmp_keys` 保持不变，向后兼容旧链路 A。

2026-09-28 review 第 1 轮修复：
1. 加 `asyncio.Semaphore(30)` —— N=200 一次打 200 并发会超 STS 默认
   QPS ~100 req/s/appid 触发 Throttling 包成 BIZ_STS_GRANT_FAILED 502；
   与 plan §5「30 并发 ≈ 1.5s」一致。
2. docstring 显式声明 per-call `session_token`（**by design**）——每
   文件一次独立 STS 签名，session_token 自然各不相同；非「共享」
   字面理解。
3. docstring 显式声明 batch 失败语义：任一文件 SDK 失败 → 整批
   502 重试（暂不实现 partial success 契约）。

2026-09-29 重构：tmp_key 模板由 `tmp/{user_id}/{sha16}/{safe_filename}`
三层简化为 `tmp/{user_id}/{sha256}.{ext}` 两层。
- `sha16` 截断 → 完整 `sha256`（64 hex，必填，schema 已 Field 校验）；
- `safe_filename` 段去掉——后端**不**做 filename 推断（中文 / 特殊字符
  / 路径穿越风险一并消失），`ext` 由前端 `StsTmpKeysRequest.ext` 显式
  传入（schema 校验 1..7 字符小写字母数字）；
- 同步：service 不再 `from core.file_hash import safe_filename`，不再
  调 `core.sts.grant_sts_tmp_key`（该函数随本次重构一并删除，详见
  `core/sts.py` 顶部 docstring）；
- 直接 `await grant_credentials_for_prefix(prefix=tmp_key, ...)`——
  prefix 语义由 core 层更新为「完整 key 前缀」（详见
  `core/sts.py::grant_credentials_for_prefix` docstring）。

2026-09-29 新增：service 层加 `x_user_id: int | None = None` 形参，
拼 tmp_key 时优先用 `x_user_id`，缺失回退 `settings.sts_default_user_id`。
由部署层 rust 转发层通过 `X-Forwarded-User-Id` header 注入（详见
`api/v1/sts.py` 顶部 docstring）。
"""

from __future__ import annotations

import asyncio
import uuid

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.sts import TMP_PREFIX_REQUIRED, grant_credentials_for_prefix
from schema.sts import (
    StsBatchTmpKeysRequest,
    StsBatchTmpKeysResponse,
    StsCredentialsOut,
    StsHealthResponse,
    StsTmpKeysRequest,
    StsTmpKeysResponse,
)

# 2026-09-28 review 第 1 轮修复：batch STS 签发并发上限。
# 30 并发 ≈ 1.5s（plan §5 风险缓解），与 STS 默认 QPS ~100 req/s/appid
# 安全余量充足，避免 Throttling 包成 BIZ_STS_GRANT_FAILED 502。
_BATCH_STS_CONCURRENCY = 30


class StsService:
    async def grant_tmp_keys(
        self,
        req: StsTmpKeysRequest,
        *,
        x_user_id: int | None = None,
    ) -> StsTmpKeysResponse:
        """签发一对 STS 临时凭证 + 返回前端直传 COS 所需的全套元数据。

        2026-09-29 重构：tmp_key 模板改为 `tmp/{uid}/{sha256}.{ext}`。
        prefix 入参（=tmp_key 自身）作为完整 key 传给
        `grant_credentials_for_prefix`，由 core 层内部补 `*` 形成 policy
        resource 通配。

        2026-09-29 新增：`x_user_id` 形参——优先用于 tmp_key 的 `{user_id}`
        段；`None` 时回退 `settings.sts_default_user_id`。
        由 api 层从 `X-Forwarded-User-Id` header 透传（rust 转发层注入）。
        """
        # 2026-09-29 重构：`content_sha256` 必填 + 64 hex（schema 已校验），
        # 直接使用；不再截前 16 hex。
        sha256 = req.content_sha256.lower()
        # 2026-09-29 新增：tmp_key 的 `{user_id}` 段优先取 `x_user_id`，
        # 缺失回退 `settings.sts_default_user_id`。
        uid = x_user_id if x_user_id is not None else settings.sts_default_user_id
        tmp_key = f"tmp/{uid}/{sha256}.{req.ext}"

        # 2026-09-29 重构：直接调 `grant_credentials_for_prefix`，不再走
        # 已删除的 `grant_sts_tmp_key` 薄包装。
        creds = await grant_credentials_for_prefix(
            prefix=tmp_key,
            expire_seconds=req.expire_seconds,
        )

        scheme = settings.cos_scheme or "https"
        endpoint = (
            settings.cos_endpoint
            or f"{scheme}://cos.{settings.cos_region}.myqcloud.com"
        )

        return StsTmpKeysResponse(
            tmp_key=tmp_key,
            bucket=settings.cos_bucket,
            region=settings.cos_region,
            endpoint=endpoint,
            scheme=scheme,
            credentials=StsCredentialsOut(**creds),
            expires_in=creds["expired_time"] - creds["start_time"],
            upload_prefix=settings.cos_upload_prefix or "drawings/",
        )

    # 2026-09-28 新增：批量 STS 临时凭证签发。
    #
    # 内部用 `asyncio.gather` 并发签 N 个文件（每文件独立 SDK 调用、各
    # 自独立的 tmp_key / session_token / start_time / expired_time；共
    # 享 bucket / region / endpoint / scheme / upload_prefix 等来自
    # settings 的字段）。
    #
    # 性能预估（plan §5 风险缓解）：单文件签名约 50ms，30 并发
    # asyncio.gather ≈ 1.5s；200 个上限 ~ 10s 上限，仍在 HTTP 30s timeout
    # 之内。
    #
    # 边界 / 校验：
    # - schema `StsBatchTmpKeysRequest.files` 已 Field(min_length=1,
    #   max_length=200)，Pydantic 失败抛 422 进不到 service 层；
    # - 这里再硬限一次（防止未来有人绕过 schema 直接构造
    #   `model_construct` 调用）。
    #
    # 2026-09-28 review 第 1 轮修复——并发限流 + 失败语义：
    # - `asyncio.Semaphore(30)` 包 `_sign_one`（见模块顶部常量
    #   `_BATCH_STS_CONCURRENCY`）；避免 N=200 一次打 200 并发超 STS 默认
    #   QPS 触发 Throttling 包成 BIZ_STS_GRANT_FAILED 502。
    # - **per-call session_token（by design）**：每文件独立 STS 签名，
    #   session_token 自然各不相同（不可能复用，因为 STS 单次签发的 token
    #   不可刷新复用）；不要把 `items[*].credentials.session_token` 理解
    #   为「共享」字段。
    # - **batch 失败语义（全有 / 全无）**：`asyncio.gather` 默认 re-raise
    #   第一条异常；任一文件 SDK 失败 → 整批 502 重试，client 拿不到任何
    #   N-1 成功文件的结果。暂不实现 partial success 契约（避免 schema
    #   跨后端漂移），如有需要应先与 frontend 同步 `BatchItems { items,
    #   failures: [...] }` 契约。
    #
    # 2026-09-29 重构：tmp_key 模板改为 `tmp/{uid}/{sha256}.{ext}`（与
    # `grant_tmp_keys` 同步）。`_sign_one` 不再调 `safe_filename`、不再
    # 截 sha16、不再走 `grant_sts_tmp_key`——直接拼 tmp_key 后调
    # `grant_credentials_for_prefix`。
    #
    # 2026-09-29 新增：service 层加 `x_user_id: int | None = None` 形参；
    # 透传给 `_sign_one`，使批量所有文件 tmp_key 共享同一个 `user_id`
    # 段（同一次 HTTP 调用复用同一个 user_id，与单文件端点语义一致）。
    async def grant_tmp_keys_batch(
        self,
        req: StsBatchTmpKeysRequest,
        *,
        x_user_id: int | None = None,
    ) -> StsBatchTmpKeysResponse:
        """批量签发 STS 临时凭证（每文件一次 SDK 签名 / 共享 bucket/region）。

        与 `grant_tmp_keys` 区别：
        - 入参是 `{scope, files[1..200]}`，每项复用 `StsTmpKeysRequest`；
        - 出参是 `{items: list[StsTmpKeysResponse]}`，每项含独立
          `tmp_key` + 共享 `bucket/region/endpoint/scheme/upload_prefix`；
        - **失败语义（全有 / 全无）**：任一文件 SDK 失败 → 整批
          BIZ_STS_GRANT_FAILED 502 重试；不要假设可拿到 partial result。

        并发上限 `_BATCH_STS_CONCURRENCY`（默认 30），与 plan §5 风险缓解
        一致；Semaphore 在 gather 外层一次性获取，避免 200 一次性 fanout。

        2026-09-29 新增：`x_user_id` 形参（keyword-only）——优先用于
        tmp_key 的 `{user_id}` 段；`None` 时回退
        `settings.sts_default_user_id`。由 api 层从
        `X-Forwarded-User-Id` header 透传（rust 转发层注入）。
        """
        if not req.files:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="files must not be empty",
                http_status=400,
            )
        if len(req.files) > 200:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"files length {len(req.files)} exceeds 200",
                http_status=400,
            )

        scheme = settings.cos_scheme or "https"
        endpoint = (
            settings.cos_endpoint
            or f"{scheme}://cos.{settings.cos_region}.myqcloud.com"
        )
        upload_prefix = settings.cos_upload_prefix or "drawings/"
        bucket = settings.cos_bucket
        region = settings.cos_region

        # 2026-09-28 review 第 1 轮修复：限流 Semaphore。`_sign_one` 内
        # 通过 `async with semaphore` 控并发；acquire 失败默认 await，
        # 不抛 CancelledError，符合 batch 失败语义。
        semaphore = asyncio.Semaphore(_BATCH_STS_CONCURRENCY)

        async def _sign_one(file_req: StsTmpKeysRequest) -> StsTmpKeysResponse:
            async with semaphore:
                # 2026-09-29 重构：完整 64 hex sha256 + 前端显式 ext，
                # 拼成 tmp_key 后直接作 prefix 入参。
                sha256 = file_req.content_sha256.lower()
                # 2026-09-29 新增：tmp_key 的 `{user_id}` 段优先取
                # `x_user_id`，缺失回退 `settings.sts_default_user_id`。
                uid = (
                    x_user_id if x_user_id is not None else settings.sts_default_user_id
                )
                tmp_key = f"tmp/{uid}/{sha256}.{file_req.ext}"
                creds = await grant_credentials_for_prefix(
                    prefix=tmp_key,
                    expire_seconds=file_req.expire_seconds,
                )
                return StsTmpKeysResponse(
                    tmp_key=tmp_key,
                    bucket=bucket,
                    region=region,
                    endpoint=endpoint,
                    scheme=scheme,
                    credentials=StsCredentialsOut(**creds),
                    expires_in=creds["expired_time"] - creds["start_time"],
                    upload_prefix=upload_prefix,
                )

        # 2026-09-28 review 第 1 轮修复：不加 `return_exceptions=True`
        # ——按 design 故意保留「任一失败整批 502」语义，避免跨后端
        # schema 漂移；docstring 已显式声明。
        items = await asyncio.gather(*(_sign_one(f) for f in req.files))
        return StsBatchTmpKeysResponse(items=list(items))

    # 2026-09-18 新增：STS 签发自检（healthcheck 探针）。
    # 真实调一次 SDK 签发，不 mock——验证整条链路（qcloud-python-sts SDK +
    # 主账号 secret_id/secret_key + CAM policy + 到 sts.tencentcloudapi.com
    # 的网络）。compose healthcheck 拿 HTTP 200 → ok；BizError 透传 →
    # healthcheck 拿到非 2xx 即 fail。
    # probe prefix 用 uuid4 hex 隔离命名空间（同一秒内多次探活也不会撞
    # CAM policy 的 resource 收口），TTL 60s——即使未被销毁也很快失效。
    async def sts_health(self) -> StsHealthResponse:
        """签发一次仅探活的 STS 凭证（expire_seconds=60）。

        - 不 PutObject / 不写 DB / 不写 Redis——零数据变更副作用。
        - 失败路径：`core.sts.grant_credentials_for_prefix` 抛
          `BizError(BIZ_STS_GRANT_FAILED, 502)` 等，本方法**不**重新包装，
          让上层（api 层）拿到原始 http_status。
        """
        probe_prefix = (
            f"{TMP_PREFIX_REQUIRED}__sts_healthcheck__/{uuid.uuid4().hex}/probe"
        )
        creds = await grant_credentials_for_prefix(
            prefix=probe_prefix,
            expire_seconds=60,
        )
        return StsHealthResponse(
            status="ok",
            probe_prefix=probe_prefix,
            expired_at=creds["expired_time"],
        )
