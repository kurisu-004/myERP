"""2026-09-17 新增：STS 端口 request/response schema。

`POST /api/v1/files/sts-tmp-keys` 请求体 + 响应体；纯数据传输 schema，
无 DB 关联。

字段约束：
- `purpose` 是有限枚举，便于 policy 跟踪 / 日志分类；
- `filename` 必填且 1..255，用于生成 tmp_key 命名；
- `content_sha256` 可选（前端算 SHA-256 截前 16 hex），缺省 fallback
  `nohash` —— 不阻塞前端粗粒度上传；
- `expire_seconds` 默认 1800、上限 `settings.sts_max_ttl_seconds`。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Purpose = Literal[
    "drawing",
    "3d_model",
    "cad_2d",
    "g_code",
    "setup_sheet",
    "assembly_master",
    "tmp",
]


class StsTmpKeysRequest(BaseModel):
    purpose: Purpose
    filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=127)
    expire_seconds: int = Field(default=1800, ge=60, le=43200)
    content_sha256: str | None = Field(
        default=None,
        min_length=16,
        max_length=64,
        description="前端算 SHA-256 后截前 16 hex；缺省 fallback 到 'nohash'",
    )


class StsCredentialsOut(BaseModel):
    tmp_secret_id: str
    tmp_secret_key: str
    session_token: str
    start_time: int
    expired_time: int


class StsTmpKeysResponse(BaseModel):
    tmp_key: str
    bucket: str
    region: str
    endpoint: str
    scheme: str
    credentials: StsCredentialsOut
    expires_in: int
    upload_prefix: str