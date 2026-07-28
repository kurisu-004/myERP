"""腾讯云 COS 客户端封装。

设计要点：
- `cos-python-sdk-v5` 是同步 SDK（基于 `requests`），所有阻塞调用
  必须用 `asyncio.to_thread` 包到默认 executor，不能直接在 async
  上下文中调用（会卡住事件循环）。
- 单进程共用一个 `CosS3Client` 实例，懒加载；可通过
  `set_cos_client_for_testing` 在测试时替换为 fake。
- 所有方法抛 `BizError(BIZ_DRAWING_UPLOAD_FAILED, 502)` 包装原始异常，
  上层 service 只关心业务错误码。
- **统一后端上传**：本进程用长期 SecretId/Key 直接调 SDK；不签临时
  URL、不走前端直传。前端要预览/下载，走 `presigned_get_url` 拿 GET
  签名即可。

本模块覆盖 COS SDK 在后端的全部典型用法（与 docs/example/cos_example.py
一一对应）：

| 上层用法                  | SDK 方法                  | 本模块入口              |
|---------------------------|---------------------------|-------------------------|
| 小文件 / 内存字节直接上传 | `put_object(Body=bytes)`  | `upload_object`         |
| 本地临时文件直传          | `put_object_from_local_file` | `upload_from_path`   |
| 大文件分块/并发上传       | `upload_file`             | `upload_file_advanced`  |
| 下载到内存                | `get_object` + `get_raw_stream()` | `download_object` |
| GET 临时签名 URL          | `get_object_url`          | `presigned_get_url`     |
| 单个 / 批量删除           | `delete_object(s)`        | `delete_object(s)`      |
| HEAD                      | `head_object`             | `head_object`           |
"""
from __future__ import annotations

import asyncio
from pathlib import Path
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


# ============================================================
# 上传
# ============================================================

async def upload_object(key: str, data: bytes, content_type: str) -> dict:
    """上传对象。`data` 是已读入内存的字节（前端走 multipart 一次性读完）。

    适合 ≤ 几十 MB 的小文件；内部走 `put_object` 简单上传（<5GB 限制）。
    大文件请改用 `upload_file_advanced`（自动分块）。
    """
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


async def upload_from_path(
    key: str,
    local_path: str | Path,
    content_type: str,
) -> dict:
    """从本地文件路径上传到 COS（SDK 推荐做法）。

    对应示例 `docs/example/cos_example.py::upload_file`，内部走
    `put_object_from_local_file`：SDK 自己用 rb 打开文件并流式 PUT，
    不会把整个文件读进进程内存，适合"前端把 multipart 落盘后由后端转传"
    这类场景。文件大小仍受 5GB 上限约束；超过请用 `upload_file_advanced`。
    """
    local = Path(local_path)
    if not local.is_file():
        # SDK 自己也会报 FileNotFoundError，但更早给出中文错误，便于排查。
        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"local file not found: {local}",
            http_status=400,
        )
    client = get_cos_client()
    return await asyncio.to_thread(
        _wrap_cos_call,
        "put_object_from_local_file",
        client.put_object_from_local_file,
        Bucket=settings.cos_bucket,
        Key=key,
        LocalFilePath=str(local),
        ContentType=content_type,
    )


async def upload_file_advanced(
    key: str,
    local_path: str | Path,
    *,
    part_size: int = 10,
    max_thread: int = 5,
) -> dict:
    """大文件分块上传（SDK 自动决定简单上传 / 分块上传）。

    对应 SDK `upload_file(Bucket, LocalFilePath, Key, PartSize, MAXThread, ...)`：
    - `PartSize`：分块大小（MB），默认 10；
    - `MAXThread`：并发上传线程数，默认 5；
    - 文件 < 5MB 走简单上传；≥ 5MB 自动分块；分块大小可调。

    仅当上传文件确实"很大"（建议 ≥ 50MB）才用，HTTP multipart 一次性读完
    的小文件用 `upload_object` / `upload_from_path` 更轻量。
    """
    local = Path(local_path)
    if not local.is_file():
        raise BizError(
            code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
            message=f"local file not found: {local}",
            http_status=400,
        )
    client = get_cos_client()
    return await asyncio.to_thread(
        _wrap_cos_call,
        "upload_file",
        client.upload_file,
        Bucket=settings.cos_bucket,
        Key=key,
        LocalFilePath=str(local),
        PartSize=part_size,
        MAXThread=max_thread,
    )


# ============================================================
# 下载
# ============================================================

async def download_object(key: str) -> bytes:
    """下载 COS 对象到内存。

    对应示例 `docs/example/cos_example.py::main` 第 3 步：先
    `client.get_object(Bucket, Key)` 拿到带 `Body` 的响应，再用
    `response['Body'].get_raw_stream().read()` 取字节流。
    """
    client = get_cos_client()
    # get_object 本身只读响应头 + 拿到 Body 这一层；真正的字节流读取
    # 仍会阻塞，所以整体丢到线程池里。
    def _do_download() -> bytes:
        resp = client.get_object(Bucket=settings.cos_bucket, Key=key)
        body = resp["Body"]
        return body.get_raw_stream().read()

    return await asyncio.to_thread(_wrap_cos_call, "get_object", _do_download)


# ============================================================
# 下载（带进程内字节缓存）
# ============================================================
#
# 设计：按 `content_sha256` 在进程内缓存 PDF / 图片字节，避免每次预览 /
# 打印都重新走 COS。
#
# - 锁只覆盖同步 OrderedDict 操作；`download_object` 仍在锁外，避免阻塞
#   事件循环。
# - 写路径更新文件即换 sha（service/part_file.py 已存 `content_sha256`），
#   旧字节随 LRU 自然淘汰，无需主动失效。
# - 大于 `_OBJECT_CACHE_MAX_BYTES` 的对象不缓存，避免 OOM。
# - 用 `OrderedDict.move_to_end` + `popitem(last=False)` 实现 LRU。
from collections import OrderedDict
from threading import Lock

_OBJECT_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_OBJECT_CACHE_MAX_BYTES = 200 * 1024 * 1024  # 200 MB 进程上限
_OBJECT_CACHE_MAX_ITEM_BYTES = 50 * 1024 * 1024  # 单个对象 > 50MB 不缓存
_OBJECT_CACHE_LOCK = Lock()


def _object_cache_stats() -> dict:
    """调试用：返回缓存当前条目数与估算字节数。"""
    with _OBJECT_CACHE_LOCK:
        return {
            "items": len(_OBJECT_CACHE),
            "approx_bytes": sum(len(v) for v in _OBJECT_CACHE.values()),
            "max_bytes": _OBJECT_CACHE_MAX_BYTES,
        }


def _object_cache_clear_for_testing() -> None:
    """测试钩子：清空缓存。"""
    with _OBJECT_CACHE_LOCK:
        _OBJECT_CACHE.clear()


async def download_object_cached(key: str, sha: str | None) -> bytes:
    """下载 COS 对象到内存，带按 `content_sha256` 的进程内 LRU 缓存。

    - `sha` 优先作为 cache key；同一 sha 跨 object_key 复用。
    - 上传时（service/part_file.py）sha 与 object_key 一一对应；
      旧版本文件被覆盖后 sha 变更，自动踢出缓存。
    - 返回的字节与 `download_object` 一致；调用方无须感知缓存。
    """
    cache_key = sha or key
    with _OBJECT_CACHE_LOCK:
        cached = _OBJECT_CACHE.get(cache_key)
        if cached is not None:
            _OBJECT_CACHE.move_to_end(cache_key)
            return cached
    # 锁外做实际下载，避免阻塞事件循环
    data = await download_object(key)
    if len(data) > _OBJECT_CACHE_MAX_ITEM_BYTES:
        # 太大不缓存，留给 COS / CDN 承担
        return data
    with _OBJECT_CACHE_LOCK:
        _OBJECT_CACHE[cache_key] = data
        _OBJECT_CACHE.move_to_end(cache_key)
        # 淘汰最旧直到 ≤ 上限
        while (
            sum(len(v) for v in _OBJECT_CACHE.values()) > _OBJECT_CACHE_MAX_BYTES
            and len(_OBJECT_CACHE) > 1
        ):
            _OBJECT_CACHE.popitem(last=False)
    return data


# ============================================================
# 临时签名 URL
# ============================================================

async def presigned_get_url(key: str, expires: int | None = None) -> str:
    """生成 GET 临时签名下载 URL。

    `expires` 单位秒；None 时用 `settings.cos_presign_expire_seconds`。
    返回的 URL 含签名参数，浏览器直接 GET 可在有效期内下载。

    使用 `get_presigned_download_url`（非 `get_object_url`）以支持自定义
    过期时间。`get_object_url` 不接收 `Expired` 参数，会报 TypeError。
    """
    client = get_cos_client()
    expire = expires if expires is not None else settings.cos_presign_expire_seconds
    return await asyncio.to_thread(
        _wrap_cos_call,
        "get_presigned_download_url",
        client.get_presigned_download_url,
        Bucket=settings.cos_bucket,
        Key=key,
        Expired=expire,
    )


# ============================================================
# 删除 / HEAD
# ============================================================

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
        # 对象不存在 → 视作 None（不抛错）；其它服务端错误 → 抛 BizError。
        if e.get_error_code() == "NoSuchKey" or e.get_status_code() == 404:
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