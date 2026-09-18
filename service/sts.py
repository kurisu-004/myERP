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
"""

from __future__ import annotations

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
    StsCredentialsOut,
    StsHealthResponse,
    StsPrefixCredentialsRequest,
    StsPrefixCredentialsResponse,
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

    # 2026-09-18 新增：内部端口（供 rust 后端按前缀签 STS 凭证）。
    # 不返回 `tmp_key`——rust 后端拿到 prefix 后自行拼对象 key。
    async def grant_prefix_credentials(
        self,
        req: StsPrefixCredentialsRequest,
    ) -> StsPrefixCredentialsResponse:
        """按调用方传入的 prefix 签一组 STS 临时凭证。

        - prefix 必须以 `tmp/` 开头 + 至少含一个子目录段（即 `tmp/`
          与 `tmp/<single>` 都拒，避免误传拿到整 tmp/ 命名空间写权），
          否则 `BIZ_STS_PREFIX_INVALID`。
        - duration 在 service 层 clamp 到 `settings.sts_max_ttl_seconds`
          （core 层仍有兜底，service clamp 是契约层声明）。
        - credentials 块复用 schema，**不**含 `tmp_key`。
        """
        prefix = req.prefix
        if not prefix.startswith(TMP_PREFIX_REQUIRED):
            raise BizError(
                code=ErrCode.BIZ_STS_PREFIX_INVALID,
                message=(f"prefix {prefix!r} must start with {TMP_PREFIX_REQUIRED!r}"),
                http_status=400,
            )
        # 2026-09-18 review：防呆——`tmp/` 后必须有非空子段，且至少含一
        # 个 `/` 边界（即不能是 `tmp/<single>`，避免误传拿到整
        # `tmp/<uid>/*` 命名空间写权；旧端点 `tmp/<uid>/<sha16>` 形态
        # 自然满足）。rest 与 slash 任一缺失即拒。
        rest = prefix[len(TMP_PREFIX_REQUIRED) :]
        if not rest or "/" not in rest:
            raise BizError(
                code=ErrCode.BIZ_STS_PREFIX_INVALID,
                message=(
                    f"prefix {prefix!r} must contain a sub-directory after "
                    f"{TMP_PREFIX_REQUIRED!r} (e.g. 'tmp/<uid>/<sha16>')"
                ),
                http_status=400,
            )

        # service 层显式 clamp：即便 Pydantic `le=43200` 已通过 schema 兜底，
        # 这里仍按 `settings.sts_max_ttl_seconds` 截断以保证运行时实际生效
        # 值与配置一致（settings 可能被 env 收紧到比 43200 更小）。
        effective_expire = min(req.expire_seconds, settings.sts_max_ttl_seconds)

        creds = await grant_credentials_for_prefix(
            prefix=req.prefix,
            expire_seconds=effective_expire,
        )

        scheme = settings.cos_scheme or "https"
        endpoint = (
            settings.cos_endpoint
            or f"{scheme}://cos.{settings.cos_region}.myqcloud.com"
        )

        return StsPrefixCredentialsResponse(
            credentials=StsCredentialsOut(**creds),
            start_time=creds["start_time"],
            expired_time=creds["expired_time"],
            expires_in=creds["expired_time"] - creds["start_time"],
            bucket=settings.cos_bucket,
            region=settings.cos_region,
            endpoint=endpoint,
            scheme=scheme,
        )

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
