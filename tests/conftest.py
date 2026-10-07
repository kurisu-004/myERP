"""测试 fixtures。

生命周期：
1. pytest session 启动 → 自动 up 一个独立的 `postgres-test` 容器（5434 端口），
   等待 healthy，把 backend-rust 的 sqlx baseline 灌进去把 schema 建好。
2. 每个测试函数用 `clean_db` fixture 自取清空后的 DB；fixture 会 truncate 所有
   业务表 + 重置流水号。
3. pytest session 结束 → `docker compose -f docker-compose.test.yml down -v`，
   容器和 volume 一并删除，下一次又是干净环境。

副作用：开发库（5433）完全不会被触及。

2026-09-24 PR-3 重构：dormant stub 全部清零。活跃测试是 10 个 unit 文件
（`tests/unit/test_{printing_service,printing_batch_request,print_back_page,
print_front_cache,file_hash,make_object_key,sts_health,sts_tmp_keys,
sts_tmp_keys_endpoint,time}.py`），假 SDK 由 `fake_cos` 提供，`_id_parse` /
`seed_root_batch` 是活跃 helper。
2026-10-08：送货单打印端口下线，服务层 / 装配件合并 / 端点共 3 个测试文件整体
删除；本仓不再有走真实 DB 的渲染器测试，`t_delivery_note` 仍留在
`_BUSINESS_TABLES`（供测试库清表用，无写入方）。
dormant 测试集合（25 个 + 25 个 unit）已整体删除，无须 _V1_DORMANT_MODULES /
_DormantStub / _V1_REMOVED_FROM_PACKAGE / _install_dormant_stubs 等兜底。

2026-09-28 alembic 全量下线：本仓 001-031 迁移链 + alembic.ini 整体 `git rm`
（schema 真相源已切到 backend-rust 的 sqlx 迁移 `backend-rust/migrations/`）。
测试库建 schema 的方式随之从 `alembic upgrade heads` 改为
`_apply_rust_baseline_sync()` 灌 backend-rust 的
`20260925000000_001_baseline.sql`；原 `_apply_pr3_test_db_patch` 删除
（baseline 已含 `t_part_batch.current_process_step_id` 列 + 索引）。
"""

from __future__ import annotations

# ============================================================
# Env override —— 必须在任何 application import 之前执行
# ============================================================
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
# （2026-09-28：`SHELF_SEED_ON_MIGRATE` 死配置已随 alembic 下线从 core/config.py
#  删除，这里也不需要再注入。）
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


# 2026-09-28 alembic 下线后，schema 的真相源是 backend-rust 的 sqlx 迁移。
# baseline 文件名固定（全量 schema 基线；后续变更走 backend-rust/migrations/ 下
# 追加的新 migration，不改 baseline 本身）。
_RUST_BASELINE_FILENAME = "20260925000000_001_baseline.sql"

# 与上面 `_probe_db_ready` 保持同一组测试库连接参数。
_TEST_DB_DSN = {
    "host": "127.0.0.1",
    "port": 5434,
    "user": "myerp_test",
    "password": "testpass",
    "database": "myerp_test",
}


def _resolve_rust_baseline() -> Path:
    """定位 backend-rust 的 sqlx baseline SQL 文件。

    2026-09-28：alembic 下线后测试库不再用 `alembic upgrade heads` 建 schema，
    改灌 backend-rust 的 `migrations/20260925000000_001_baseline.sql`。

    路径解析要同时适配两种布局（backend-python 的 git worktree 就在
    `backend-python/.claude/worktrees/<slug>/` 下，此时 `PROJECT_ROOT.parent`
    指向 `.../backend-python/.claude/worktrees`，**不是** hsh-erp 根）：
    从 `PROJECT_ROOT` 起逐级向上，每层都试 `<该层>/backend-rust/migrations/`，
    第一个命中的即返回；`RUST_MIGRATIONS_DIR` 环境变量可显式指定目录
    （直接指向 `backend-rust/migrations`），便于 CI / 非常规布局。
    """
    env_dir = _os.environ.get("RUST_MIGRATIONS_DIR")
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir) / _RUST_BASELINE_FILENAME)
    for base in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        candidates.append(base / "backend-rust" / "migrations" / _RUST_BASELINE_FILENAME)
    for cand in candidates:
        if cand.is_file():
            return cand
    tried = "\n  ".join(str(c) for c in candidates)
    raise RuntimeError(
        "找不到 backend-rust 的 sqlx baseline 文件 "
        f"{_RUST_BASELINE_FILENAME}；已依次尝试：\n  {tried}\n"
        "请确认本仓与 backend-rust 同级，或用环境变量 RUST_MIGRATIONS_DIR "
        "显式指向 backend-rust/migrations 目录。"
    )


async def _apply_rust_baseline_sync() -> None:
    """把 backend-rust 的 sqlx baseline 灌进临时测试库。

    2026-09-28 alembic 下线：schema 真相源切到 backend-rust 的 sqlx 迁移
    （`backend-rust/migrations/20260925000000_001_baseline.sql`），本仓不再
    持有迁移链，测试库建 schema 的方式随之改为整段执行这份 baseline。

    baseline 是纯 `pg_dump --schema-only` 风格：开头 `SET default_tablespace` /
    `SET default_table_access_method`，无 psql 元命令（`\\restrict` 等）、无
    `$$` dollar-quote、无 DROP / CREATE EXTENSION / OWNER TO / GRANT / ROLE，
    因此可在**空库**上用 asyncpg 的 simple query 协议（`conn.execute(sql)`，
    无参数）一次执行整段多语句。

    **刻意不复用 `core.database.engine` 的连接池**：baseline 里的
    `SET default_tablespace` 等是 session 级设置，留在池连接上会污染后续
    ORM 查询用的连接。这里单开一条连完即关。

    **本函数只在临时测试库（docker-compose.test.yml 起的 5434 容器）上跑，
    生产库永不执行。**
    """
    import asyncpg

    baseline = _resolve_rust_baseline()
    sql = baseline.read_text(encoding="utf-8")
    conn = await asyncpg.connect(**_TEST_DB_DSN)
    try:
        await conn.execute(sql)
    except Exception as e:
        raise RuntimeError(
            f"执行 backend-rust sqlx baseline 失败（文件：{baseline}）：{e}"
        ) from e
    finally:
        await conn.close()


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
        raise RuntimeError(f"failed to wipe {test_data} after 5 attempts") from last_err
    test_data.mkdir(parents=True, exist_ok=True)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _postgres_test_lifecycle():
    """整场测试只跑一次：down -v → wipe bind mount → up -d → wait healthy → 建 schema → tests → down -v。

    2026-09-16 PR-2：环境变量 `SKIP_TEST_DB_LIFECYCLE=1` 时跳过整个 docker 编排
    （包括结尾 down -v）。用于开发者在本地手动起好 test 容器后直接跑 pytest，
    绕开 Docker Desktop + virtiofs 下「wipe 后立刻 up -d 偶发 bind-mount 失败」
    的环境问题（PG initdb 报 `mkdir '/var/lib/postgresql/18/'` 失败）；
    调用方需自行保证容器在 5434 端口 healthy。

    2026-09-28：建 schema 的一步由 `alembic upgrade heads` 改为灌 backend-rust
    的 sqlx baseline（`_apply_rust_baseline_sync`）。
    """
    if _os.environ.get("SKIP_TEST_DB_LIFECYCLE") == "1":
        # 开发者在外部已经把容器起好了；等 SELECT 1 通 + 灌 baseline 建 schema。
        await _probe_db_ready()
        await _apply_rust_baseline_sync()
        print("[test-db] rust baseline applied (external container)")
        yield
        return

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
    print("[test-db] container is already running")

    # 5. 再用真实 SELECT 1 探一次。pg_isready 可能过早 healthy（PG 启动早期会断连）。
    await _probe_db_ready()
    print("[test-db] DB is queryable")

    # 6. 建 schema：灌 backend-rust 的 sqlx baseline（2026-09-28 取代 alembic upgrade heads）
    await _apply_rust_baseline_sync()
    print("[test-db] rust baseline applied")

    yield  # ---- tests run here ----

    # 7. session 结束：清容器 + 清 volume
    down = _compose("down", "-v")
    print(f"[test-db] docker compose down -v:\n{down.stdout.strip()}")


# ============================================================
# Session / clean_db fixtures
# ============================================================

# 业务表清单（按"先子后父"顺序 truncate，避免 FK 冲突；本项目无物理 FK，
# 但仍按依赖顺序保持稳定）。
#
# 2026-09-24 PR-3 重构：dormant 业务表（t_pickup_skip_event / t_outsource_* /
# t_process / t_work_type* / t_worker / t_shelf / t_user* / t_role_menu /
# t_menu / t_applicant / t_delivery_note_event / t_delivery_note_counter /
# t_part_event / t_process_chain_step 等）已下线，仅保留 7 张活跃表。
_BUSINESS_TABLES = (
    # 第一批：child rows + 多态子表
    "t_part_file",
    "t_part_batch",
    "t_assembly",
    "t_part",
    "t_delivery_note",
    # 第二批：字典 / 计数器
    "t_serial_counter",
    "t_customer",
)


async def seed_root_batch(session: AsyncSession, part) -> "object":
    """2026-07-29 批次化：给直接 session.add(TPart) 的 fixture 补根批次。

    服务层所有流转都走批次；测试夹具若绕过 create_part 直接插 t_part 行，
    必须配套一条 batch_no=1 的根批次（镜像 status/location/holder/quantity）。

    2026-09-16 t_part 瘦身（Rust 迁移 027）：t_part 的 location /
    current_holder_id / placed_at / delivery_note_id 列已删。夹具若要给
    根批次指定位置/holder，把这些值作为 **transient 属性** 挂在 part 实例上
    （`part.location = ...`，与 repository 的 last_inspection_fail_note
    同款约定），这里用 getattr 镜像进批次；不挂则批次对应字段为 NULL。

    2026-09-16 PR-3（Rust 迁移 028）：t_part_batch 的 `next_process_id` /
    `placed_at` 列也已删，改为 `current_process_step_id`（→ t_process_chain_step.id）。
    t_part.next_process_id 仍保留（rollup 物化列），但 fixture 端通常无工艺链
    关联，传 None 即可。
    """
    from model import TPartBatch

    batch = TPartBatch(
        part_id=part.id,
        batch_no=1,
        quantity=part.quantity,
        status=part.status,
        location=getattr(part, "location", None),
        current_holder_id=getattr(part, "current_holder_id", None),
        current_process_step_id=getattr(part, "current_process_step_id", None),
        delivery_note_id=getattr(part, "delivery_note_id", None),
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
    # 复位流水号计数器。2026-09-28 起灌的是 backend-rust 的 sqlx baseline
    # （schema-only，不含 A-Z 种子行），所以这里必须无条件 ON CONFLICT 重灌。
    await session.execute(text("SELECT 1 FROM t_serial_counter LIMIT 0"))  # 探测表存在
    await session.execute(
        text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "SELECT chr(ascii('A') + i), 0 "
            "FROM generate_series(0, 25) i "
            "ON CONFLICT (prefix) DO UPDATE SET counter = 0"
        )
    )
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

    2026-09-24 PR-3：仍被 `tests/unit/test_printing_service.py` +
    `tests/unit/test_print_back_page.py` + `tests/unit/test_print_front_cache.py`
    消费，保留。
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    put_calls: list[tuple[str, str, int, str]] = field(default_factory=list)
    put_from_path_calls: list[tuple[str, str, int, str]] = field(default_factory=list)
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
        self,
        Bucket,
        Key,
        LocalFilePath,
        **kwargs,  # noqa: N803
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
        self.put_from_path_calls.append((Bucket, Key, len(data), content_type))
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
        self.upload_advanced_calls.append((Bucket, Key, len(data), PartSize, MAXThread))
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
