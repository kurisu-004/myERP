"""2026-09-17 新增：STS 临时凭证 service（薄层）。

只做参数整理 + 拼 `tmp_key` + 注入 endpoint / scheme / 上传前缀等响应
字段；不持有 session / 不写 DB（无状态）。BIZError 由 `core.sts` 直接抛。
"""
from __future__ import annotations

from core.config import settings
from core.file_hash import safe_filename
from core.sts import grant_sts_tmp_key
from schema.sts import (
    StsCredentialsOut,
    StsTmpKeysRequest,
    StsTmpKeysResponse,
)


class StsService:
    async def grant_tmp_keys(self, req: StsTmpKeysRequest) -> StsTmpKeysResponse:
        """签发一对 STS 临时凭证 + 返回前端直传 COS 所需的全套元数据。"""
        sha16 = (req.content_sha256 or "nohash")[:16].lower()
        safe_name = safe_filename(req.filename)
        tmp_key = (
            f"tmp/{settings.sts_default_user_id}/{sha16}/{safe_name}"
        )

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