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
2026-09-28 删除 `sts-prefix-credentials` 内部端口——rust 后端
upload_session 域下线后已无调用方；详见 plan
`sts-session-uploader-sts-sts-sequential-globe` §2.2。

2026-09-18 review 第 1 轮修复：prefix schema 加 `field_validator` 拒绝
`*` / `?` / `..` / `\\x00` 等通配 / 路径穿越字符。

2026-09-28 review 第 2 轮修复：移除 schema 入口对 `TMP_PREFIX_REQUIRED`
的死引用（2026-09-28 删 `sts-prefix-credentials` 内部端口后，`schema.sts`
不再消费该常量；service 层仍直接 `from core.sts import TMP_PREFIX_REQUIRED`，
无依赖 cycle 风险，故这里删除导入并从 `__all__` 移除）。

2026-09-18 新增 `StsHealthResponse`：STS 签发自检端点响应契约——
healthcheck 探针（`GET /api/v1/files/sts-health`）真实调一次 SDK 签发
成功后回执（status="ok" + probe_prefix + expired_at），供 compose
healthcheck 按 HTTP 状态判定通过 / 失败。

2026-09-28 新增 `StsBatchTmpKeysRequest` / `StsBatchTmpKeysResponse`：
扩展 `POST /api/v1/files/sts-tmp-keys` 接受 `files[]` 数组入参（单
HTTP + 单签名批），用于批量上传场景。`scope` 字符串用作业务边界（如
`photos/{scope}/...`），不参与 policy 签发；`files` 是
`list[StsTmpKeysRequest]`，长度硬限 200（与 plan §5 风险缓解一致），
复用单文件 schema 的所有字段约束（purpose / filename / content_type
/ expire_seconds / content_sha256）。
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


# 2026-09-28 删除：内部端口 `StsPrefixCredentialsRequest` /
# `StsPrefixCredentialsResponse`（2026-09-18 新增，2026-09-28 随
# `sts-prefix-credentials` 端点下线而删除）。前缀校验仍由
# `core.sts.grant_credentials_for_prefix` 内部持有（被 healthcheck 探
# 针复用）；外部 schema 入口已撤。


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


# 2026-09-28 新增：批量 STS 临时凭证请求 schema。
# 扩展 `POST /api/v1/files/sts-tmp-keys` 接受数组入参（单 HTTP +
# 单签名批），用于前端批量上传场景（一次拿 N 个 tmp_key 与共享
# bucket/region/scheme）。
#
# 设计取舍：
# - `scope` 仅作为业务边界 metadata（前端用 `parts_new` / `parts_edit`
#   等标识上传业务域），不参与 policy 签发；policy 仍按每个文件 sha16
#   单独收口到 `tmp/{uid}/{sha16}/*`，与单文件端点行为完全一致。
# - `files` 复用 `StsTmpKeysRequest` 形态，**复用其内部字段校验**
#   （filename 长度 / purpose 枚举 / expire_seconds 上下界 / content_sha256
#   长度）。每个文件独立签一次 SDK，得到自己的 tmp_key + session_token
#   + start_time / expired_time。
# - `Field(max_length=200)` 硬限批大小，与 plan §5 风险缓解一致
#   （Pydantic 失败抛 422，进不到 service 层）。
class StsBatchTmpKeysRequest(BaseModel):
    scope: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "业务域标识（如 `parts_new` / `parts_edit`），仅 metadata，"
            "不影响 policy；用于前端日志分类 / 后端审计。"
        ),
    )
    files: list[StsTmpKeysRequest] = Field(
        min_length=1,
        max_length=200,
        description=(
            "待签发的文件列表（1..200）。每项复用 StsTmpKeysRequest "
            "schema，独立签 STS / 独立 tmp_key / 共享 bucket/region/scheme。"
        ),
    )


# 2026-09-28 新增：批量 STS 临时凭证响应 schema。
# `items` 是与 `files` 等长的 list；每项是完整 `StsTmpKeysResponse`
# （tmp_key / bucket / region / endpoint / scheme / credentials 五元组 /
# expires_in / upload_prefix）。
#
# 设计取舍：
# - 不返回 batch 级 `bucket` / `region` / `scheme` 共享字段——逐项重复
#   即可，调用方（前端 CosUploader）按 item 直接喂给上传器，无需再做
#   跨 item 字段对齐；重复字段字节开销在 200 个文件以内可忽略。
# - 不返回 `scope` 回显——`scope` 是 metadata，不需要服务端回执给前端。
class StsBatchTmpKeysResponse(BaseModel):
    items: list[StsTmpKeysResponse]


# 2026-09-28 新增：`POST /api/v1/files/sts-tmp-keys` 端点入参 Union 类型。
# 端点接受「单文件 schema（向后兼容旧链路 A）」或「批量 schema（新
# 批量场景）」之一，由 Pydantic v2 smart union 自动区分。
# 区分规则（取决于数据形态，不是显式 tag 字段）：
# - 单文件 schema 含 `purpose` + `filename`（StsTmpKeysRequest 必填）
#   且不含 `files` → 匹配 `StsTmpKeysRequest`；
# - 含 `scope` + `files` → 匹配 `StsBatchTmpKeysRequest`；
# - 其它形态（缺关键字段 / 字段混搭）→ 422 ValidationError。
#
# response_model 端用同样思路返回 `StsTmpKeysResponse` 或
# `StsBatchTmpKeysResponse`。
StsTmpKeysEndpointBody = StsTmpKeysRequest | StsBatchTmpKeysRequest
StsTmpKeysEndpointResponse = StsTmpKeysResponse | StsBatchTmpKeysResponse


__all__ = [
    "Purpose",
    "StsBatchTmpKeysRequest",
    "StsBatchTmpKeysResponse",
    "StsCredentialsOut",
    "StsHealthResponse",
    "StsTmpKeysEndpointBody",
    "StsTmpKeysEndpointResponse",
    "StsTmpKeysRequest",
    "StsTmpKeysResponse",
]
