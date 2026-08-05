"""测试 fixtures。

生命周期：
1. pytest session 启动 → 自动 up 一个独立的 `postgres-test` 容器（5434 端口），
   等待 healthy，跑 `alembic upgrade head` 把 schema 建好。
2. 每个测试函数用 `clean_db` fixture 自取清空后的 DB；fixture 会 truncate 所有
   业务表 + 重置流水号。
3. pytest session 结束 → `docker compose -f docker-compose.test.yml down -v`，
   容器和 volume 一并删除，下一次又是干净环境。

副作用：开发库（5433）完全不会被触及。
"""
from __future__ import annotations

# ============================================================
# Env override —— 必须在任何 application import 之前执行
# ============================================================
# core.database 模块加载时会读 `settings.database_url`，而 `Settings()` 实例化
# 会从环境变量取 `DATABASE_URL`。所以这一段必须放在所有 application import 之前，
# 否则 engine 会指向开发库。
import os as _os

_os.environ["DATABASE_URL"] = _os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://myerp_test:testpass@127.0.0.1:5434/myerp_test",
)
# JWT / COS 用安全的测试占位即可，fake_cos fixture 会替换真正的 SDK 调用。
_os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-32bytes-pad")
_os.environ.setdefault("JWT_ISSUER", "myerp-test")
_os.environ.setdefault("COS_SECRET_ID", "test-cos-id")
_os.environ.setdefault("COS_SECRET_KEY", "test-cos-key")
_os.environ.setdefault("COS_BUCKET", "test-bucket")
_os.environ.setdefault("COS_REGION", "ap-guangzhou")
# 不在迁移时自动 seed t_user / t_shelf；测试自己造数据。
_os.environ.setdefault("SHELF_SEED_ON_MIGRATE", "false")
_os.environ.setdefault("TZ", "Asia/Shanghai")

# ============================================================
# 接下来才是正常 import
# ============================================================
import asyncio
import json
import subprocess
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import core.cos as cos_mod
from core.database import SessionLocal

# ============================================================
# Test container lifecycle
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.test.yml"


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def _wait_container_healthy(timeout: float = 60.0) -> None:
    """轮询 `docker compose ps --format json`，等 Health=healthy。"""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = _compose("ps", "--format", "json", check=False)
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("Health") == "healthy":
                        return
        except (subprocess.SubprocessError, json.JSONDecodeError) as e:
            last_err = e
        time.sleep(1.0)
    raise RuntimeError(
        f"test postgres container did not become healthy within {timeout}s "
        f"(last error: {last_err})"
    )


async def _probe_db_ready(timeout: float = 30.0) -> None:
    """healthy 只是端口通了；PG 启动初期仍会断连。再用 SELECT 1 探一次确认能跑查询。"""
    import asyncpg

    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = await asyncpg.connect(
                host="127.0.0.1",
                port=5434,
                user="myerp_test",
                password="testpass",
                database="myerp_test",
            )
            await conn.execute("SELECT 1")
            await conn.close()
            return
        except Exception as e:
            last_err = e
            await asyncio.sleep(1.0)
    raise RuntimeError(
        f"test postgres not queryable within {timeout}s (last error: {last_err})"
    )


def _run_alembic_upgrade_head_sync() -> None:
    """在测试容器上跑迁移。env 已经在 conftest 顶部注入过了。

    必须在独立线程里跑——alembic 内部用了 asyncio.run()，不能在已有 event loop 里调。

    用 `upgrade heads`（复数）而不是 `upgrade head`：当前 alembic 拓扑有
    `schema/003 + prod_data/002` 两个 head；用 `head`（单数）会报
    `Multiple head revisions` 错误。
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option(
        "sqlalchemy.url", _os.environ["DATABASE_URL"]
    )  # alembic.ini 留空时保险起见再设一次
    command.upgrade(cfg, "heads")


def _wipe_test_data_dir() -> None:
    """把宿主机上 `data/postgres-test/` 整个删掉。

    重要：`docker compose down -v` 不会清 bind mount 的宿主机目录（这是 by design）。
    上次跑测试如果异常退出，PG 18 的数据目录（`data/postgres-test/18/docker/`）会残留。
    再次起容器时，PG 检测到 `PG_VERSION` 已存在就直接复用，**忽略**新的
    POSTGRES_USER/PASSWORD/DB 环境变量——结果就是新的用户名/密码没生效，TCP
    连接认证失败。

    所以 pre-flight 必须把 bind mount 源目录整个删掉，让 PG initdb 全新跑一遍。

    重试：Docker Desktop（virtiofs）在上一个容器 down 后可能短暂持有目录句柄，
    rmtree 会撞 `OSError: [Errno 66] Directory not empty`（2026-08-05 实测：
    上一批 pytest 刚 down -v、立刻起下一批时偶发，整批 ERROR）。最多重试 5 次。
    """
    import shutil

    test_data = PROJECT_ROOT / "data" / "postgres-test"
    last_err: OSError | None = None
    for attempt in range(5):
        try:
            if test_data.exists():
                shutil.rmtree(test_data)
                if attempt:
                    print(f"[test-db] wiped stale {test_data} (attempt {attempt + 1})")
                else:
                    print(f"[test-db] wiped stale {test_data}")
            break
        except OSError as e:
            last_err = e
            time.sleep(1.0)
    else:
        raise RuntimeError(
            f"failed to wipe {test_data} after 5 attempts"
        ) from last_err
    test_data.mkdir(parents=True, exist_ok=True)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _postgres_test_lifecycle():
    """整场测试只跑一次：down -v → wipe bind mount → up -d → wait healthy → migrate → tests → down -v。"""
    # 1. 兜底：上次异常退出可能残留容器，先 down -v 停掉并清匿名 volume / 旧容器。幂等。
    #    必须先于 wipe——若旧容器仍挂着 bind mount 运行，rmtree 会与 PG 写入竞态，
    #    报 OSError: [Errno 66] Directory not empty（2026-08-05 实测踩坑）。
    _compose("down", "-v", check=False)

    # 2. 清掉宿主机上残留的 PG 数据目录（bind mount 源）。
    #    docker 不会清 bind mount 源目录，但 PG initdb 会复用残留数据导致新 env
    #    （POSTGRES_USER/PASSWORD/DB）失效。所以 down 之后我们自己删。
    _wipe_test_data_dir()

    # 3. 起新容器
    up = _compose("up", "-d")
    print(f"[test-db] docker compose up -d:\n{up.stdout.strip()}")

    # 4. 等 healthy（docker healthcheck）
    _wait_container_healthy()
    print("[test-db] container is healthy")

    # 5. 再用真实 SELECT 1 探一次。pg_isready 可能过早 healthy（PG 启动早期会断连）。
    await _probe_db_ready()
    print("[test-db] DB is queryable")

    # 6. 跑迁移（推到线程池，避开已有 event loop）
    await asyncio.to_thread(_run_alembic_upgrade_head_sync)
    print("[test-db] alembic upgrade head done")

    yield  # ---- tests run here ----

    # 6. session 结束：清容器 + 清 volume
    down = _compose("down", "-v")
    print(f"[test-db] docker compose down -v:\n{down.stdout.strip()}")


# ============================================================
# Session / clean_db fixtures
# ============================================================

# 业务表清单（按"先子后父"顺序 truncate，避免 FK 冲突；本项目无物理 FK，
# 但仍按依赖顺序保持稳定）。
_BUSINESS_TABLES = (
    "t_pickup_skip_event",  # 2026-08-05：跳序事件（append-only，按 created_at 依赖 part/worker）
    "t_outsource_quote_event",
    "t_outsource_quote",
    "t_part_event",
    "t_part_batch",
    "t_work_type_process",
    "t_part_file",
    "t_part",
    "t_assembly",
    "t_serial_counter",
    # 第二批：fixture / 字典类
    "t_process",
    "t_work_type",
    "t_worker",
    "t_shelf",
    "t_customer",
    "t_user",
    "t_user_role",
    "t_role_menu",
    "t_menu",
)


async def seed_root_batch(session: AsyncSession, part) -> "object":
    """2026-07-29 批次化：给直接 session.add(TPart) 的 fixture 补根批次。

    服务层所有流转都走批次；测试夹具若绕过 create_part 直接插 t_part 行，
    必须配套一条 batch_no=1 的根批次（镜像 status/location/holder/quantity）。
    """
    from model import TPartBatch

    batch = TPartBatch(
        part_id=part.id,
        batch_no=1,
        quantity=part.quantity,
        status=part.status,
        location=part.location,
        current_holder_id=part.current_holder_id,
        next_process_id=part.next_process_id,
        placed_at=part.placed_at,
        delivery_note_id=part.delivery_note_id,
    )
    session.add(batch)
    await session.flush()
    return batch


async def _truncate_all(session: AsyncSession) -> None:
    for table in _BUSINESS_TABLES:
        # t_customer 已改为雪花 ID（t_customer_id_seq 已 DROP），
        # 不能用 RESTART IDENTITY；其余 BigSerial 表保持原样。
        suffix = "" if table == "t_customer" else " RESTART IDENTITY CASCADE"
        await session.execute(text(f'TRUNCATE TABLE "{table}"{suffix}'))
    # 复位流水号计数器；alembic 迁移可能没 seed，单独 ensure 一次。
    # 2026-07-09 起：seed A-Z 全 26 行（迁移 000000000014），不再限于 L/F/H。
    await session.execute(text("SELECT 1 FROM t_serial_counter LIMIT 0"))  # 探测表存在
    await session.execute(text(
        "INSERT INTO t_serial_counter (prefix, counter) "
        "SELECT chr(ascii('A') + i), 0 "
        "FROM generate_series(0, 25) i "
        "ON CONFLICT (prefix) DO UPDATE SET counter = 0"
    ))
    await session.commit()


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
    """在测试开始前清空所有业务表，并复位流水号计数器。"""
    await _truncate_all(db_session)
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


# ============================================================
# 抑制 lifespan 副作用：测试环境不需要 dashboard push loop
# ============================================================
# core.database.lifespan 会在 FastAPI startup 时启动后台 dashboard 推送任务，
# 单元测试不会真正启动 app，但万一某个 fixture 触发了 app 创建，把这一行打开。
# 目前无需主动设置，留 placeholder 以便排查。