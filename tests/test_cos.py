"""`core/cos` 客户端封装单元测试。

测试策略：直接构造 FakeCosClient → 注入 `core.cos._cos_client` → 调
async 包装函数；不依赖 DB。所有用例都跑在内存里，不发真实网络请求。
"""
from __future__ import annotations

import pytest

import core.cos as cos_mod
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError


pytestmark = pytest.mark.asyncio


# ============================================================
# upload_from_path —— 镜像 docs/example/cos_example.py::upload_file
# ============================================================
async def test_upload_from_path_writes_object_and_records_call(
    fake_cos, tmp_path
) -> None:
    """本地文件 → put_object_from_local_file → key 落到内存桶。"""
    src = tmp_path / "drawing.pdf"
    payload = b"%PDF-1.4\nfake bytes\n"
    src.write_bytes(payload)

    out = await cos_mod.upload_from_path(
        "drawings/part/1/1.pdf", src, "application/pdf"
    )

    assert out == {"ETag": "fake-etag", "Key": "drawings/part/1/1.pdf"}
    assert fake_cos.objects["drawings/part/1/1.pdf"] == payload
    assert fake_cos.put_from_path_calls == [
        (settings.cos_bucket, "drawings/part/1/1.pdf", len(payload),
         "application/pdf"),
    ]


async def test_upload_from_path_missing_file_raises(fake_cos, tmp_path) -> None:
    """本地路径不存在 → BizError(400)，不发任何 SDK 调用。"""
    missing = tmp_path / "ghost.step"
    with pytest.raises(BizError) as exc_info:
        await cos_mod.upload_from_path("drawings/part/1/1.step", missing, "")
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_UPLOAD_FAILED
    assert exc_info.value.http_status == 400
    assert fake_cos.put_from_path_calls == []


async def test_upload_from_path_cos_failure_wraps(fake_cos, tmp_path) -> None:
    """SDK 抛错 → BizError(502) 包装，原始异常链保留。"""
    src = tmp_path / "a.step"
    src.write_bytes(b"x")
    fake_cos.fail_on_put.add("drawings/part/1/1.step")

    with pytest.raises(BizError) as exc_info:
        await cos_mod.upload_from_path("drawings/part/1/1.step", src, "")
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_UPLOAD_FAILED
    assert exc_info.value.http_status == 502


# ============================================================
# upload_file_advanced —— 大文件分块上传
# ============================================================
async def test_upload_file_advanced_records_part_size(
    fake_cos, tmp_path
) -> None:
    """upload_file 把 PartSize / MAXThread 透传给 SDK。"""
    src = tmp_path / "big.step"
    src.write_bytes(b"x" * 1024)

    await cos_mod.upload_file_advanced(
        "drawings/part/1/big.step", src, part_size=5, max_thread=8
    )
    assert fake_cos.objects["drawings/part/1/big.step"] == b"x" * 1024
    bucket, key, size, part_size, max_thread = (
        fake_cos.upload_advanced_calls[0]
    )
    assert key == "drawings/part/1/big.step"
    assert size == 1024
    assert part_size == 5
    assert max_thread == 8


async def test_upload_file_advanced_default_part_size(
    fake_cos, tmp_path
) -> None:
    """不传 PartSize/MAXThread → 用 10/5 默认值。"""
    src = tmp_path / "m.step"
    src.write_bytes(b"hi")

    await cos_mod.upload_file_advanced("k", src)
    _, _, _, part_size, max_thread = fake_cos.upload_advanced_calls[0]
    assert part_size == 10
    assert max_thread == 5


# ============================================================
# download_object —— 镜像 docs/example/cos_example.py 内存下载
# ============================================================
async def test_download_object_returns_bytes(fake_cos) -> None:
    fake_cos.objects["drawings/part/1/1.pdf"] = b"PDF-CONTENT"

    data = await cos_mod.download_object("drawings/part/1/1.pdf")

    assert data == b"PDF-CONTENT"
    assert fake_cos.download_calls == ["drawings/part/1/1.pdf"]


async def test_download_object_missing_key_raises_502(fake_cos) -> None:
    with pytest.raises(BizError) as exc_info:
        await cos_mod.download_object("drawings/missing")
    assert exc_info.value.code == ErrCode.BIZ_DRAWING_UPLOAD_FAILED
    assert exc_info.value.http_status == 502


# ============================================================
# presigned_get_url —— 既有用法回归
# ============================================================
async def test_presigned_get_url_default_expire(fake_cos) -> None:
    url = await cos_mod.presigned_get_url("drawings/part/1/1.pdf")
    assert "drawings/part/1/1.pdf" in url
    bucket, key, expired = fake_cos.presigned_get_calls[0]
    assert key == "drawings/part/1/1.pdf"
    # None 时走 settings.cos_presign_expire_seconds（900）；具体值由 .env 决定。
    assert expired in (900,)


async def test_presigned_get_url_custom_expire(fake_cos) -> None:
    await cos_mod.presigned_get_url("k", expires=60)
    _, _, expired = fake_cos.presigned_get_calls[0]
    assert expired == 60


# ============================================================
# delete / head —— 既有用法回归（service 层间接覆盖过，这里仅冒烟）
# ============================================================
async def test_delete_objects_batch(fake_cos) -> None:
    fake_cos.objects["a"] = b"1"
    fake_cos.objects["b"] = b"2"
    await cos_mod.delete_objects(["a", "b"])
    assert "a" not in fake_cos.objects
    assert "b" not in fake_cos.objects


async def test_head_object_existing_and_missing(fake_cos) -> None:
    fake_cos.objects["k"] = b"hello"
    assert (await cos_mod.head_object("k")) == {"Content-Length": 5}
    assert await cos_mod.head_object("missing") is None