"""腾讯云 COS 客户端封装。

设计要点：
- `cos-python-sdk-v5` 是同步 SDK（基于 `requests`），所有阻塞调用
  必须用 `asyncio.to_thread` 包到默认 executor，不能直接在 async
  上下文中调用（会卡住事件循环）。
- 单进程共用一个 `CosS3Client` 实例，懒加载；可通过
  `set_cos_client_for_testing` 在测试时替换为 fake。
- 所有方法抛 `BizError(BIZ_DRAWING_UPLOAD_FAILED, 502)` 包装原始异常，
  上层 service 只关心业务错误码。
"""
from __future__ import annotations

import asyncio
from typing import Any

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError


_cos_client: CosS3Client | None = None


def get_cos_client() -> CosS3Client:
    """获取（或惰性创建）进程级 CosS3Client 单例。"""
    global _cos_client
    if _cos_client is None:
        cfg_kwargs: dict[str, Any] = {
            "Region": settings.cos_region,
            "SecretId": settings.cos_secret_id,
            "SecretKey": settings.cos_secret_key,
            "Scheme": settings.cos_scheme,
        }
        if settings.cos_endpoint:
            cfg_kwargs["Endpoint"] = settings.cos_endpoint
        cfg = CosConfig(**cfg_kwargs)
        _cos_client = CosS3Client(cfg)
    return _cos_client


def set_cos_client_for_testing(client: CosS3Client | None) -> None:
    """测试钩子：替换 / 重置单例。"""
    global _cos_client
    _cos_client = client


def _wrap_cos_call(label: str, fn, /, *args, **kwargs):
    """包一层 try/except，把 SDK 异常转成 BizError。"""
    try:
        return fn(*args, **kwargs)
    except (CosClientError, CosServiceError) as e:
        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"COS {label} failed: {e}",
            http_status=502,
        ) from e
    except Exception as e:
        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"COS {label} unexpected error: {e!r}",
            http_status=502,
        ) from e


async def upload_object(key: str, data: bytes, content_type: str) -> dict:
    """上传对象。`data` 是已读入内存的字节（前端走 multipart 一次性读完）。"""
    client = get_cos_client()
    return await asyncio.to_thread(
        _wrap_cos_call,
        "put_object",
        client.put_object,
        Bucket=settings.cos_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


async def delete_object(key: str) -> None:
    """删除单个对象；对象不存在时 SDK 不抛错（幂等）。"""
    client = get_cos_client()
    await asyncio.to_thread(
        _wrap_cos_call,
        "delete_object",
        client.delete_object,
        Bucket=settings.cos_bucket,
        Key=key,
    )


async def delete_objects(keys: list[str]) -> None:
    """批量删除（最多 1000 / 批，循环处理）。失败抛 BIZ_DRAWING_UPLOAD_FAILED。"""
    if not keys:
        return
    client = get_cos_client()
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        objs = [{"Key": k} for k in batch]
        await asyncio.to_thread(
            _wrap_cos_call,
            "delete_objects",
            client.delete_objects,
            Bucket=settings.cos_bucket,
            Delete={"Object": objs, "Quiet": True},
        )


async def presigned_get_url(key: str, expires: int | None = None) -> str:
    """生成 GET 临时签名 URL。

    `expires` 单位秒；None 时用 `settings.cos_presign_expire_seconds`。
    返回的 URL 含签名参数，浏览器直接 GET 可在有效期内下载。
    """
    client = get_cos_client()
    expire = expires if expires is not None else settings.cos_presign_expire_seconds
    return await asyncio.to_thread(
        _wrap_cos_call,
        "get_object_url",
        client.get_object_url,
        Bucket=settings.cos_bucket,
        Key=key,
        Expired=expire,
    )


async def head_object(key: str) -> dict | None:
    """HEAD 对象。对象不存在时返回 None（不抛错）。"""
    client = get_cos_client()
    try:
        return await asyncio.to_thread(
            client.head_object,
            Bucket=settings.cos_bucket,
            Key=key,
        )
    except CosServiceError as e:
        if e.get("Code") == "NoSuchKey" or e.get("status_code") == 404:
            return None
        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"COS head_object failed: {e}",
            http_status=502,
        ) from e
    except CosClientError as e:
        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"COS head_object failed: {e}",
            http_status=502,
        ) from e