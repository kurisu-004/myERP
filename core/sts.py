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

2026-09-18 抽出公共函数 `grant_credentials_for_prefix`：原
`grant_sts_tmp_key` 与新增 `grant_sts_prefix_credentials` 共享底层签名
逻辑，policy actions 保持 7 个不变，resource 仅跟随 prefix 收窄。
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


# 2026-09-18：两个端点共享的 prefix 校验 / TTL 兜底常量。
_TMP_PREFIX_REQUIRED: Final[str] = "tmp/"


async def grant_credentials_for_prefix(
    *,
    prefix: str,
    expire_seconds: int,
) -> dict:
    """按指定 prefix 签一组 STS 临时凭证（仅上传类 7 个 action）。

    Parameters
    ----------
    prefix:
        已含命名空间的 key 前缀，例如 `tmp/{user_id}/{sha16}` 或
        `tmp/{user_id}/{session_id}`（rust 后端 session 用）。**不含**
        尾部 `/*` 通配符，函数内部补。
    expire_seconds:
        TTL（秒）。clamp 到 `settings.sts_max_ttl_seconds`，下限 60。
        入参非法（< 60）抛 `BIZ_INVALID_VALUE`。

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

    Raises
    ------
    BizError(BIZ_INVALID_VALUE)
        当 `expire_seconds < 60`。
    BizError(BIZ_STS_GRANT_FAILED, 502)
        SDK 抛错 / 响应缺 credentials。
    """
    if expire_seconds < 60:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"expire_seconds {expire_seconds} < 60",
            http_status=400,
        )
    expire_seconds = min(int(expire_seconds), settings.sts_max_ttl_seconds)

    bucket_appid = settings.cos_bucket
    region = settings.cos_region
    appid = bucket_appid.rsplit("-", 1)[-1]
    # resource 收窄到 `{prefix}*`（prefix 已含命名空间，如 `tmp/{user_id}/{sha16}`）。
    # 注意：这里**不**插入 `{bucket_appid}/` 中段——腾讯云 CAM 同时接受
    # `qcs::cos:{region}:uid/{appid}:{bucket}/{prefix}*` 与
    # `qcs::cos:{region}:uid/{appid}:{prefix}*` 两种写法，去掉 bucket 段
    # 让 prefix 完全来自调用方，policy 文本更短、便于审计。
    resource = f"qcs::cos:{region}:uid/{appid}:{prefix}*"
    policy = {
        "version": "2.0",
        "statement": [
            {
                "effect": "allow",
                "action": list(_UPLOAD_ACTIONS_FULL),
                "resource": [resource],
            }
        ],
    }

    def _do_grant() -> dict:
        # SDK Sts 类把 secret_id/secret_key/bucket/region/policy/allow_actions
        # 等都通过构造 config 字典传入；get_credential() 无参数。
        client = _CosSts(
            {
                "secret_id": settings.cos_secret_id,
                "secret_key": settings.cos_secret_key,
                "duration_seconds": expire_seconds,
                "bucket": bucket_appid,
                "region": region,
                "policy": policy,
            }
        )
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


async def grant_sts_tmp_key(
    *,
    purpose: str,
    filename: str,
    sha16: str,
    expire_seconds: int,
) -> dict:
    """每次请求现签 STS 临时凭证（前端直传 COS 用）。

    复用 `grant_credentials_for_prefix`，resource 收窄到
    `tmp/{user_id}/{sha16}*` 单目录。

    Parameters
    ----------
    purpose:
        业务目的（`drawing` / `3d_model` / ...），保留以兼容 service
        调用方，**不再影响 policy**（2026-09-18 抽出公共函数后）。
    filename:
        已 ASCII 折叠的安全文件名，保留以兼容 service 调用方，**不再影响
        policy**——policy resource 路径前缀只用 `tmp/{user_id}/{sha16}`，
        不含 filename。
    sha16:
        内容 SHA-256 的前 16 hex；用于隔离不同文件上传目录（policy 收口）。
    expire_seconds:
        TTL（秒）。超过 `settings.sts_max_ttl_seconds` 自动回退到上限；
        小于 60 抛 `BIZ_INVALID_VALUE`。
    """
    user_id = settings.sts_default_user_id
    key_prefix = f"tmp/{user_id}/{sha16}"
    return await grant_credentials_for_prefix(
        prefix=key_prefix,
        expire_seconds=expire_seconds,
    )


__all__ = [
    "_TMP_PREFIX_REQUIRED",
    "grant_credentials_for_prefix",
    "grant_sts_tmp_key",
]
