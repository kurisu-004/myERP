"""测试 fixtures。

- `db_session`：请求级 session，commit/rollback 行为与生产一致。
- `clean_db`：在每个测试开始前清空业务表。
  **会破坏本地 DB 数据**，跑测试前确认本地是可重置状态。
- `fake_cos`：注入一个内存版的 CosS3Client 到 `core.cos`，所有 put/delete/presign
  都不发真实网络请求；记录调用次数供断言。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import core.cos as cos_mod
from core.database import SessionLocal


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def clean_db(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    # 顺序：先 truncate 子记录，再 truncate 父记录；t_assembly 与 t_part 都依赖 t_customer。
    await db_session.execute(text("TRUNCATE TABLE t_part_event RESTART IDENTITY"))
    await db_session.execute(text("TRUNCATE TABLE t_drawing_file RESTART IDENTITY"))
    await db_session.execute(text("TRUNCATE TABLE t_part RESTART IDENTITY"))
    await db_session.execute(text("TRUNCATE TABLE t_assembly RESTART IDENTITY"))
    await db_session.execute(
        text("TRUNCATE TABLE t_serial_counter RESTART IDENTITY")
    )
    await db_session.execute(
        text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "VALUES ('L', 0), ('F', 0), ('H', 0)"
        )
    )
    await db_session.commit()
    yield db_session


# ============================================================
# 假 COS 客户端
# ============================================================
@dataclass
class FakeCosClient:
    """内存版 COS 客户端，覆盖 SDK 在后端调到的所有方法。

    与 `core.cos` 中包装的方法一一对应：
    - put_object / put_object_from_local_file / upload_file（写入）
    - get_object（下载到内存）
    - get_object_url / get_presigned_url（GET/PUT 预签 URL）
    - head_object / delete_object / delete_objects（管理操作）
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    put_calls: list[tuple[str, str, int, str]] = field(default_factory=list)
    put_from_path_calls: list[tuple[str, str, int, str]] = field(
        default_factory=list
    )
    upload_advanced_calls: list[tuple[str, str, int, int, int]] = field(
        default_factory=list
    )
    download_calls: list[str] = field(default_factory=list)
    presigned_get_calls: list[tuple[str, str, int]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    head_calls: list[str] = field(default_factory=list)
    # 模拟故障注入：把 key 写在这里的会抛 BizError
    fail_on_put: set[str] = field(default_factory=set)

    # ---------- 写入 ----------
    def put_object(self, Bucket, Key, Body, **kwargs):  # noqa: N803
        if Key in self.fail_on_put:
            from core.cos import _wrap_cos_call
            from core.error_code import ErrCode
            from core.exception import BizError

            def _raise(*a, **kw):
                raise BizError(
                    code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
                    message=f"simulated COS put failure on {Key}",
                    http_status=502,
                )

            _wrap_cos_call("put_object", _raise)
        data = Body.read() if hasattr(Body, "read") else Body
        if isinstance(data, str):
            data = data.encode()
        self.objects[Key] = data
        content_type = kwargs.get("ContentType", "")
        self.put_calls.append((Bucket, Key, len(data), content_type))
        return {"ETag": "fake-etag", "Key": Key}

    def put_object_from_local_file(  # noqa: N803
        self, Bucket, Key, LocalFilePath, **kwargs  # noqa: N803
    ):
        if Key in self.fail_on_put:
            from core.cos import _wrap_cos_call
            from core.error_code import ErrCode
            from core.exception import BizError

            def _raise(*a, **kw):
                raise BizError(
                    code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
                    message=f"simulated COS put failure on {Key}",
                    http_status=502,
                )

            _wrap_cos_call("put_object_from_local_file", _raise)
        with open(LocalFilePath, "rb") as f:
            data = f.read()
        self.objects[Key] = data
        content_type = kwargs.get("ContentType", "")
        self.put_from_path_calls.append(
            (Bucket, Key, len(data), content_type)
        )
        return {"ETag": "fake-etag", "Key": Key}

    def upload_file(  # noqa: N803
        self,
        Bucket,
        Key,
        LocalFilePath,
        PartSize=10,  # noqa: N803
        MAXThread=5,  # noqa: N803
        **kwargs,
    ):
        # fake 实现：与 put_object_from_local_file 等价，仅多记一份参数。
        if Key in self.fail_on_put:
            from core.cos import _wrap_cos_call
            from core.error_code import ErrCode
            from core.exception import BizError

            def _raise(*a, **kw):
                raise BizError(
                    code=ErrCode.BIZ_DRAWING_UPLOAD_FAILED,
                    message=f"simulated COS upload_file failure on {Key}",
                    http_status=502,
                )

            _wrap_cos_call("upload_file", _raise)
        with open(LocalFilePath, "rb") as f:
            data = f.read()
        self.objects[Key] = data
        self.upload_advanced_calls.append(
            (Bucket, Key, len(data), PartSize, MAXThread)
        )
        return {"ETag": "fake-etag", "Key": Key}

    # ---------- 下载 ----------
    def get_object(self, Bucket, Key, **kwargs):  # noqa: N803
        self.download_calls.append(Key)
        if Key not in self.objects:
            from qcloud_cos.cos_exception import CosServiceError

            raise CosServiceError(
                "get_object",
                {"code": "NoSuchKey", "message": "NoSuchKey"},
                404,
            )
        return _FakeGetObjectResponse(self.objects[Key])

    # ---------- 预签 URL ----------
    def get_object_url(self, Bucket, Key, **kwargs):  # noqa: N803
        expired = kwargs.get("Expired", 0)
        self.presigned_get_calls.append((Bucket, Key, expired))
        return f"https://fake.cos.example/{Bucket}/{Key}?signature=fake"

    # ---------- HEAD / 删除 ----------
    def head_object(self, Bucket, Key, **kwargs):  # noqa: N803
        self.head_calls.append(Key)
        if Key not in self.objects:
            from qcloud_cos.cos_exception import CosServiceError

            raise CosServiceError(
                "head_object",
                {"code": "NoSuchKey", "message": "NoSuchKey"},
                404,
            )
        return {"Content-Length": len(self.objects[Key])}

    def delete_object(self, Bucket, Key, **kwargs):  # noqa: N803
        self.delete_calls.append(Key)
        self.objects.pop(Key, None)
        return {}

    def delete_objects(self, Bucket, Delete, **kwargs):  # noqa: N803
        objs = Delete.get("Object", [])
        for o in objs:
            k = o["Key"]
            self.delete_calls.append(k)
            self.objects.pop(k, None)
        return {"Deleted": [{"Key": o["Key"]} for o in objs]}


class _FakeGetObjectResponse:
    """`client.get_object(...)` 返回值的最小可用形态。

    只实现 `core.cos.download_object` 用到的 `response['Body']
    .get_raw_stream().read()` 一条路径。
    """

    def __init__(self, data: bytes) -> None:
        self._data = data

    def __getitem__(self, key: str):
        if key != "Body":
            raise KeyError(key)
        return self

    def get_raw_stream(self) -> "_FakeRawStream":
        return _FakeRawStream(self._data)


class _FakeRawStream:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest_asyncio.fixture
async def fake_cos():
    """替换 core.cos._cos_client 为 FakeCosClient，测试结束自动还原。"""
    fake = FakeCosClient()
    original = cos_mod._cos_client
    cos_mod.set_cos_client_for_testing(fake)
    try:
        yield fake
    finally:
        cos_mod.set_cos_client_for_testing(original)