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
`asyncio.gather` 并发调 `core.sts.grant_sts_tmp_key`（每文件一次签名，
共享 bucket/region/scheme / endpoint 等独立字段）。单文件入口
`grant_tmp_keys` 保持不变，向后兼容旧链路 A。
"""

from __future__ import annotations

import asyncio
import uuid

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.file_hash import safe_filename
from core.sts import (
    TMP_PREFIX_REQUIRED,
    grant_credentials_for_prefix,
    grant_sts_tmp_key,
)
from schema.sts import (
    StsBatchTmpKeysRequest,
    StsBatchTmpKeysResponse,
    StsCredentialsOut,
    StsHealthResponse,
    StsTmpKeysRequest,
    StsTmpKeysResponse,
)


class StsService:
    async def grant_tmp_keys(self, req: StsTmpKeysRequest) -> StsTmpKeysResponse:
        """签发一对 STS 临时凭证 + 返回前端直传 COS 所需的全套元数据。"""
        sha16 = (req.content_sha256 or "nohash")[:16].lower()
        safe_name = safe_filename(req.filename)
        tmp_key = f"tmp/{settings.sts_default_user_id}/{sha16}/{safe_name}"

        creds = await grant_sts_tmp_key(
            purpose=req.purpose,
            filename=safe_name,
            sha16=sha16,
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
    # 复用 `grant_sts_tmp_key`（不删除，单文件路径继续使用），内部用
    # `asyncio.gather` 并发签 N 个文件（每文件独立 SDK 调用、各自独立的
    # tmp_key / session_token / start_time / expired_time；共享
    # bucket / region / endpoint / scheme / upload_prefix 等来自 settings
    # 的字段）。
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
    async def grant_tmp_keys_batch(
        self,
        req: StsBatchTmpKeysRequest,
    ) -> StsBatchTmpKeysResponse:
        """批量签发 STS 临时凭证（每文件一次 SDK 签名 / 共享 bucket/region）。

        与 `grant_tmp_keys` 区别：
        - 入参是 `{scope, files[1..200]}`，每项复用 `StsTmpKeysRequest`；
        - 出参是 `{items: list[StsTmpKeysResponse]}`，每项含独立
          `tmp_key` + 共享 `bucket/region/endpoint/scheme/upload_prefix`。
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

        async def _sign_one(file_req: StsTmpKeysRequest) -> StsTmpKeysResponse:
            sha16 = (file_req.content_sha256 or "nohash")[:16].lower()
            safe_name = safe_filename(file_req.filename)
            tmp_key = f"tmp/{settings.sts_default_user_id}/{sha16}/{safe_name}"
            creds = await grant_sts_tmp_key(
                purpose=file_req.purpose,
                filename=safe_name,
                sha16=sha16,
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
