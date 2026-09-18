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
逻辑。

2026-09-18 review 第 1 轮修复：
- resource 段恢复为 `qcs::cos:{region}:uid/{appid}:{bucket_appid}/{prefix}*`
  （含 bucket 段，与旧端点 `grant_sts_tmp_key` 行为对齐）。
- SDK config 同时传 `allow_prefix` + `allow_actions` + `policy`（仅 policy
  生效，allow_* 是 SDK 死路径但保留以维持旧行为兼容性；详见
  「SDK config 变更说明」段）。
- core 层加 `prefix` 兜底（必须以 `tmp/` 开头）；service 层再校验至少
  含一个子目录段。

SDK config 变更说明
-------------------
`qcloud-python-sts` SDK 行为（`.venv/lib/python3.12/site-packages/sts/sts.py`
解析逻辑）：
- 解析 config 时若同时给 `policy` + `allow_prefix` / `allow_actions`，三
  字段都会被读到 `self`，但 `get_credential()` 走 `self.policy` 分支，
  `allow_*` 不参与构造最终 policy —— 即 `allow_*` 在 policy 同时存在时
  是死代码。
- 反之若只给 `allow_prefix` / `allow_actions`（不传 policy），SDK 会用
  `bucket` + `allow_prefix` 自动拼 resource 字符串
  `qcs::cos:{region}:uid/{appid}:{bucket}{prefix}`（prefix 强制补前导
  `/`）。

本仓坚持把 resource 显式写入 `policy.statement[0].resource`（不受 SDK
自动拼接逻辑影响，便于审计），但**保留** `allow_prefix` + `allow_actions`
两个字段：
1. 对齐旧端点（`grant_sts_tmp_key`）config 形状，减少后续运维 / review
   diff；
2. 显式标注允许前缀 / 动作，万一 SDK 解析逻辑在后续版本变化，policy
   仍显式安全。

CAM resource 两种写法的兼容说明：腾讯云 CAM 接受
`uid/{appid}:{bucket}/{prefix}*` 与 `uid/{appid}:{prefix}*` 两种 resource
格式（policy 语法层面等价），详见腾讯云 CAM 文档：
https://cloud.tencent.com/document/product/598/10603
（CAM 策略结构 / Resource 元素语法）。

本仓选用含 bucket 段的写法 —— 旧端点行为 + 同 appid 多 bucket 场景下
policy 收口更明确。
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
# 2026-09-18 review：提升为 public（去掉前导下划线），service / schema 复用。
TMP_PREFIX_REQUIRED: Final[str] = "tmp/"


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
        当 `expire_seconds < 60` 或 `prefix` 不以 `tmp/` 开头（core 层
        兜底，正常路径 service 层已挡）。
    BizError(BIZ_STS_GRANT_FAILED, 502)
        SDK 抛错 / 响应缺 credentials。
    """
    # 2026-09-18 review：core 层兜底 —— 防止 service / 上层未来绕过校验
    # 直接传任意 prefix 进来。仅校验「以 tmp/ 开头」这一最弱约束；更强
    # 的「至少含子目录段」校验放在 service 层（schema 已约束 max_length
    # 与非法字符）。
    if not prefix.startswith(TMP_PREFIX_REQUIRED):
        raise BizError(
            code=ErrCode.BIZ_STS_PREFIX_INVALID,
            message=f"prefix {prefix!r} must start with {TMP_PREFIX_REQUIRED!r}",
            http_status=400,
        )

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
    # 2026-09-18 review：恢复 bucket 段（与旧端点 `grant_sts_tmp_key` 行
    # 为对齐）。resource = `qcs::cos:{region}:uid/{appid}:{bucket}/{prefix}*`。
    resource = f"qcs::cos:{region}:uid/{appid}:{bucket_appid}/{prefix}*"
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
        # 2026-09-18 review：恢复 allow_prefix + allow_actions（policy
        # 同时存在时 SDK 走 self.policy 分支，allow_* 是死代码但保留，
        # 详见模块 docstring「SDK config 变更说明」）。
        client = _CosSts(
            {
                "secret_id": settings.cos_secret_id,
                "secret_key": settings.cos_secret_key,
                "duration_seconds": expire_seconds,
                "bucket": bucket_appid,
                "region": region,
                "allow_prefix": [f"{prefix}/*"],
                "allow_actions": list(_UPLOAD_ACTIONS_SHORT),
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

    SDK config 变更说明（2026-09-18 review 第 1 轮修复）
    --------------------------------------------------
    本次重构（0507aa7）把 resource 由
    `qcs::cos:{region}:uid/{appid}:{bucket_appid}/{key_prefix}/*` 改成
    `qcs::cos:{region}:uid/{appid}:{prefix}*`、删除了 SDK config 中的
    `allow_prefix` / `allow_actions` 字段。本次 review 已恢复：
    - resource 恢复含 bucket 段（与本函数 PR 引入时行为一致）；
    - SDK config 恢复 `allow_prefix=[f"{prefix}/*"]` + `allow_actions=
      list(_UPLOAD_ACTIONS_SHORT)`（policy 同时存在时 SDK 走 self.policy
      分支，allow_* 实际不影响最终 policy；详见模块 docstring）。
    """
    user_id = settings.sts_default_user_id
    key_prefix = f"tmp/{user_id}/{sha16}"
    return await grant_credentials_for_prefix(
        prefix=key_prefix,
        expire_seconds=expire_seconds,
    )


__all__ = [
    "TMP_PREFIX_REQUIRED",
    "grant_credentials_for_prefix",
    "grant_sts_tmp_key",
]
