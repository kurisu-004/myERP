"""2026-09-17 新增：STS 端口 request/response schema。

`POST /api/v1/files/sts-tmp-keys` 请求体 + 响应体；纯数据传输 schema，
无 DB 关联。

字段约束：
- `purpose` 是有限枚举，便于 policy 跟踪 / 日志分类；
- `filename` 必填且 1..255，用于生成 tmp_key 命名；
- `content_sha256` 可选（前端算 SHA-256 截前 16 hex），缺省 fallback
  `nohash` —— 不阻塞前端粗粒度上传；
- `expire_seconds` 默认 1800、上限 `settings.sts_max_ttl_seconds`。

2026-09-18 新增 `POST /api/v1/files/sts-prefix-credentials`（内部端口——
供 rust 后端按前缀签凭证）：prefix 必须以 `tmp/` 开头；out schema 复
用 `StsCredentialsOut` 凭证块，不返回 `tmp_key`（rust 后端自行拼对象
key）。

2026-09-18 review 第 1 轮修复：prefix schema 加 `field_validator` 拒绝
`*` / `?` / `..` / `\\x00` 等通配 / 路径穿越字符；导出
`TMP_PREFIX_REQUIRED` 常量供 service 复用，避免跨模块从 `core.sts`
import 私有常量。

2026-09-18 新增 `StsHealthResponse`：STS 签发自检端点响应契约——
healthcheck 探针（`GET /api/v1/files/sts-health`）真实调一次 SDK 签发
成功后回执（status="ok" + probe_prefix + expired_at），供 compose
healthcheck 按 HTTP 状态判定通过 / 失败。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.sts import TMP_PREFIX_REQUIRED

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


# 2026-09-18 新增：内部端口（供 rust 后端按前缀签 STS 凭证）。
# 2026-09-18 review：加 field_validator 拒绝通配 / 路径穿越字符（防止
# rust 后端误传 `*` / `..` 等导致 policy 收口失效）。
class StsPrefixCredentialsRequest(BaseModel):
    prefix: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "COS key 前缀（含命名空间，不含尾部 /* 通配符）。"
            "必须以 `tmp/` 开头 + 至少含一个子目录段，否则 service 层"
            "返回 BIZ_STS_PREFIX_INVALID；含 `*` / `?` / `..` / `\\x00`"
            " 也直接被 schema 拦截。"
        ),
    )
    expire_seconds: int = Field(default=1800, ge=60, le=43200)

    @field_validator("prefix")
    @classmethod
    def _reject_glob_and_traversal_chars(cls, v: str) -> str:
        """拒绝通配 / 路径穿越字符。

        拒绝集合：
        - `*` / `?`：COS / 腾讯云 CAM 通配符，prefix 段不允许通配
        - `..`：相对路径穿越
        - NUL（`\\x00`）：终止符（防字符串注入 / 截断）
        - 反斜杠 `\\`：强制只用正斜杠分层（避免 Windows 风格穿越）

        以 `tmp/` 开头 / 至少一个子目录段的语义校验交给 service 层
        （service 返回 BIZ_STS_PREFIX_INVALID 4xx，便于 schema 报错与
        业务报错分离）。
        """
        if not v:
            raise ValueError("prefix must not be empty")
        if any(ch in v for ch in ("*", "?", "..", "\\", "\x00")):
            raise ValueError(
                "prefix must not contain glob ('*' / '?') / traversal "
                "('..') / NUL / backslash ('\\') characters"
            )
        return v


# 2026-09-18 新增：内部端口响应。无 `tmp_key`（rust 后端自行派生对象 key），
# 直接复用 `StsCredentialsOut` 凭证块。
class StsPrefixCredentialsResponse(BaseModel):
    credentials: StsCredentialsOut
    start_time: int
    expired_time: int
    expires_in: int
    bucket: str
    region: str
    endpoint: str
    scheme: str


# 2026-09-18 新增：STS 签发自检端点响应（healthcheck 探针）。
# 与 prefix 内部端口响应解耦——本响应只关心「SDK 是否真签通了」+ probe
# 元数据，不返回凭证五元组（避免健康检查路径泄漏临时凭证；调用方是 compose
# healthcheck，不是业务客户端）。
# status 固定为字面量 "ok"——失败路径由 BizError 透传（非 2xx），不进
# response_model。
class StsHealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    probe_prefix: str
    expired_at: int


__all__ = [
    "TMP_PREFIX_REQUIRED",
    "Purpose",
    "StsCredentialsOut",
    "StsHealthResponse",
    "StsPrefixCredentialsRequest",
    "StsPrefixCredentialsResponse",
    "StsTmpKeysRequest",
    "StsTmpKeysResponse",
]
