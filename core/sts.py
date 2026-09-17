"""2026-09-17 新增：STS 临时凭证端口（前端直传 COS 用）。

调用 `qcloud-python-sts` 官方 SDK（pip 包名 `qcloud-python-sts`，
import 名 `sts`），每次请求现签不缓存。SDK 同步阻塞走
`asyncio.to_thread` 包装，避免卡事件循环。

策略（CAM policy）：
- 资源限定到 `tmp/{user_id}/{sha16}/*` 单目录，禁止通配到整个桶。
- 允许的动作限定上传用 7 个（PutObject / InitiateMultipartUpload /
  ListMultipartUploads / ListParts / UploadPart / CompleteMultipartUpload /
  AbortMultipartUpload），不含 GetObject / DeleteObject —— 读取仍走后端
  `files/{id}/download-url` 代理，删除由后端 service 统一处理。

异常一律包装为 `BizError(BIZ_STS_GRANT_FAILED, 502)`，上层（service /
api）只关心业务错误码。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Final

from sts.sts import Sts as _CosSts

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError

logger = logging.getLogger(__name__)


# 2026-09-17 新增：STS 临时凭证允许的动作集合（仅上传 + 分块，不含 Get/Delete）。
# SDK 的 `allow_actions` 字段用短名（去掉 `name/cos:` 前缀）。
_UPLOAD_ACTIONS_FULL: Final[tuple[str, ...]] = (
    "name/cos:PutObject",
    "name/cos:InitiateMultipartUpload",
    "name/cos:ListMultipartUploads",
    "name/cos:ListParts",
    "name/cos:UploadPart",
    "name/cos:CompleteMultipartUpload",
    "name/cos:AbortMultipartUpload",
)
_UPLOAD_ACTIONS_SHORT: Final[tuple[str, ...]] = tuple(
    s.split("/", 1)[1] for s in _UPLOAD_ACTIONS_FULL
)


async def grant_sts_tmp_key(
    *,
    purpose: str,
    filename: str,
    sha16: str,
    expire_seconds: int,
) -> dict:
    """每次请求现签 STS 临时凭证。

    Parameters
    ----------
    purpose:
        业务目的（`drawing` / `3d_model` / ...）；目前仅用于日志分类，不
        影响 policy。
    filename:
        已 ASCII 折叠的安全文件名，仅用于拼 `tmp_key` 路径与日志；policy
        resource 路径前缀只用 `tmp/{user_id}/{sha16}`，**不含** filename。
    sha16:
        内容 SHA-256 的前 16 hex；用于隔离不同文件上传目录（policy 收口）。
    expire_seconds:
        TTL（秒）。超过 `settings.sts_max_ttl_seconds` 自动回退到上限；
        小于 60 抛 `BIZ_INVALID_VALUE`。

    Returns
    -------
    dict 形如：
        {
            "tmp_secret_id": str,
            "tmp_secret_key": str,
            "session_token": str,
            "start_time": int,
            "expired_time": int,
        }
    """
    expire_seconds = max(60, min(int(expire_seconds), settings.sts_max_ttl_seconds))

    bucket_appid = settings.cos_bucket
    region = settings.cos_region
    appid = bucket_appid.rsplit("-", 1)[-1]
    user_id = settings.sts_default_user_id
    key_prefix = f"tmp/{user_id}/{sha16}"

    resource = (
        f"qcs::cos:{region}:uid/{appid}:{bucket_appid}/{key_prefix}/*"
    )
    policy = {
        "version": "2.0",
        "statement": [{
            "effect": "allow",
            "action": list(_UPLOAD_ACTIONS_FULL),
            "resource": [resource],
        }],
    }

    def _do_grant() -> dict:
        # SDK Sts 类把 secret_id/secret_key/bucket/region/policy/allow_actions
        # 等都通过构造 config 字典传入；get_credential() 无参数。
        client = _CosSts({
            "secret_id": settings.cos_secret_id,
            "secret_key": settings.cos_secret_key,
            "duration_seconds": expire_seconds,
            "bucket": bucket_appid,
            "region": region,
            "allow_prefix": [f"{key_prefix}/*"],
            "allow_actions": list(_UPLOAD_ACTIONS_SHORT),
            "policy": policy,
        })
        return client.get_credential()

    try:
        result = await asyncio.to_thread(_do_grant)
    except Exception as e:
        # 2026-09-17 P2-5：详细异常写 server log，只把异常类型名暴露给客户端，
        # 避免 SDK 原始 dict repr（含临时凭证 / policy 痕迹）泄漏到响应里。
        logger.exception("STS grant failed")
        raise BizError(
            code=ErrCode.BIZ_STS_GRANT_FAILED,
            message=f"STS grant failed: {type(e).__name__}",
            http_status=502,
        ) from e

    # SDK 返回的 credentials 三元组字段名固定（camelCase），其它返回 startTime/
    # expiredTime（int timestamp）。_backwardCompat 已统一小驼峰。
    try:
        creds = result["credentials"]
    except KeyError as e:
        raise BizError(
            code=ErrCode.BIZ_STS_GRANT_FAILED,
            message=f"STS grant missing credentials: {result!r}",
            http_status=502,
        ) from e

    return {
        "tmp_secret_id": creds["tmpSecretId"],
        "tmp_secret_key": creds["tmpSecretKey"],
        "session_token": creds["sessionToken"],
        "start_time": int(result["startTime"]),
        "expired_time": int(result["expiredTime"]),
    }