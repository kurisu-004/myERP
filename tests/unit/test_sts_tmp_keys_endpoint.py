"""2026-09-28 review 第 1 轮修复新增 / 2026-09-29 重构：
`POST /api/v1/files/sts-tmp-keys` 端到端测试（TestClient + httpx）。

plan §4 #9 硬要求：本端点必须有 TestClient 端到端覆盖。本文件对齐 plan
验收条件，覆盖 4 个场景：

1. 单文件 schema（向后兼容旧链路 A）→ 200 + `StsTmpKeysResponse` 形状；
2. 批量 schema（`{scope, files[1..200]}`）→ 200 + `StsBatchTmpKeysResponse`
   形状（`items` 长度对齐 `files`）；
3. `files=[]` → 422 ValidationError（Pydantic smart union 拒收批量 schema
   + min_length=1）；422 响应结构对齐前端类型契约（含 `detail` 数组）；
4. `files` 长度=201 → 422 ValidationError（Pydantic max_length=200）。

2026-09-29 重构：tmp_key 模板简化为 `tmp/{uid}/{sha256}.{ext}`——
本测试请求体同步加 `content_sha256`（必填 64 hex）+ `ext`（必填）；
响应 `tmp_key` 断言相应更新（无 filename 段、`ext` 后缀）。

2026-09-29 新增：`X-Forwarded-User-Id` header 透传到 service 层：
- `endpoint_with_x_forwarded_user_id_header`：带 header `67890` →
  tmp_key = `tmp/67890/{sha}.pdf`；
- `endpoint_without_x_forwarded_user_id_header`：不带 header → tmp_key
  = `tmp/1/{sha}.pdf`（fallback `sts_default_user_id=1`）；
- `endpoint_with_malformed_user_id_header`：带 header `abc`（非 int）
  → 422 FastAPI 标准校验失败响应。

实现策略：本端点零 DB IO（`StsService` 不持 session / 不写 DB），故每个
测试单独 `FastAPI()` + `include_router(sts.router, prefix="/api/v1")`
跑 TestClient；`get_sts_service` 不挂 `Depends(get_session)`，
直接 `return StsService()`，无需 DB stub。

2026-09-28 review：单测者必须用「boundary schema 测试 Pydantic v2 smart
union」——不能只测 happy path；同时验证：
- `response_model=StsTmpKeysEndpointResponse`（Union）在
  Pydantic v2 下的响应序列化能产出 batch 的 `items` 字段（而不是被压平成
  单文件 schema）；
- 422 响应是 FastAPI 标准结构（`detail` 数组），不是 backend-python
  自定义信封。

不依赖 docker / DB / Redis。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.sts as sts_core

# 2026-09-29 重构：统一 sha fixture，与 test_sts_tmp_keys.py 对齐。
SHA_64_A = "a" * 64
SHA_64_B = "b" * 64


# ============================================================
# Mock SDK（与 test_sts_tmp_keys.py 形态一致，独立注册避免跨文件 fixture 耦合）
# ============================================================
@dataclass
class _FakeStsCall:
    config: dict[str, Any]


@dataclass
class _FakeStsClient:
    config: dict[str, Any]
    recorder: _FakeStsRecorder

    def get_credential(self) -> dict[str, Any]:
        self.recorder.calls.append(_FakeStsCall(config=dict(self.config)))
        start = 1700000000
        call_idx = self.config.get("_call_idx", 0)
        return {
            "credentials": {
                "tmpSecretId": f"STS.fake-id-{call_idx}",
                "tmpSecretKey": "fake-key",
                "sessionToken": f"fake-token-{call_idx}",
            },
            "startTime": start,
            "expiredTime": start + self.config["duration_seconds"],
        }


@dataclass
class _FakeStsRecorder:
    calls: list[_FakeStsCall] = field(default_factory=list)


def _patch_sdk_with_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakeStsRecorder:
    """monkeypatch 后 `_FakeStsClient` 工厂：每次注入递增 `_call_idx` 让
    `tmpSecretId` 后缀可定位。"""
    recorder = _FakeStsRecorder()

    def _factory(config: dict[str, Any]) -> _FakeStsClient:
        config_with_idx = dict(config)
        config_with_idx["_call_idx"] = len(recorder.calls)
        return _FakeStsClient(config=config_with_idx, recorder=recorder)

    monkeypatch.setattr(sts_core, "_CosSts", _factory)
    return recorder


def _build_app() -> FastAPI:
    """构造一个最小 FastAPI app：只挂 sts router（不挂 printing 端点，
    免去 DB stub）。

    路径前缀对齐真实部署：`api_router -> /api` + `v1.api_router -> /v1`
    + `sts.router -> /files` + handler `/sts-tmp-keys` → 真实路径
    `/api/v1/files/sts-tmp-keys`。include_router 的 prefix 不会和 router
    自身的 prefix 重复叠加，所以这里用 `/api/v1`（与 api_router +
    v1.api_router 等效）即可。
    """
    from api.v1 import sts as sts_module

    app = FastAPI()
    app.include_router(sts_module.router, prefix="/api/v1")
    return app


# ============================================================
# 场景 1：单文件 schema（向后兼容旧链路 A）
# ============================================================
def test_single_file_schema_returns_200_with_keys_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-28 review：单文件 `{purpose, filename, ...}` → 200 +
    `StsTmpKeysResponse` 形状（含 `tmp_key` / `credentials` 五元组 /
    `expires_in` / `upload_prefix`）。回归旧链路 A 兼容。

    2026-09-29 重构：请求体加必填 `content_sha256` + `ext`；响应
    `tmp_key` 形如 `tmp/{uid}/{sha256}.{ext}`。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "expire_seconds": 1800,
            "content_sha256": SHA_64_A,
            "ext": "pdf",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    # StsTmpKeysResponse 必填字段
    assert "tmp_key" in body
    # 2026-09-29 重构：tmp_key 形如 `tmp/{uid}/{sha256}.{ext}`，无
    # filename 段。
    expected_key = f"tmp/1/{SHA_64_A}.pdf"
    assert body["tmp_key"] == expected_key
    assert body["tmp_key"].startswith("tmp/")
    assert body["tmp_key"].endswith(".pdf")
    # 关键不变量：filename 段已去除——断言用 sha64 后 48 hex + ".pdf"
    # 整段（filename 不会进 key；SHA="a"*64 + ext="pdf" 虽自然产生
    # "...aaa.pdf" 子串，但精确等值断言已锁死模板，filename 不参与）。
    assert body["tmp_key"].endswith(f"{SHA_64_A[16:]}.pdf")
    # credentials 五元组
    creds = body["credentials"]
    for key in (
        "tmp_secret_id",
        "tmp_secret_key",
        "session_token",
        "start_time",
        "expired_time",
    ):
        assert key in creds, f"missing credentials.{key}"
    # 共享字段
    for key in (
        "bucket",
        "region",
        "endpoint",
        "scheme",
        "expires_in",
        "upload_prefix",
    ):
        assert key in body, f"missing {key}"


# ============================================================
# 场景 2：批量 schema → 200 + StsBatchTmpKeysResponse.items 长度对齐 files
# ============================================================
def test_batch_schema_returns_200_with_items_length_aligned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-28 review：批量 `{scope, files[2]}` → 200 + 响应顶层
    `items` 字段、长度=2、每项是完整 `StsTmpKeysResponse`。

    2026-09-29 重构：files 每项加必填 `content_sha256` + `ext`。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "scope": "parts_new",
            "files": [
                {
                    "purpose": "drawing",
                    "filename": "a.pdf",
                    "content_type": "application/pdf",
                    "expire_seconds": 1800,
                    "content_sha256": SHA_64_A,
                    "ext": "pdf",
                },
                {
                    "purpose": "drawing",
                    "filename": "b.pdf",
                    "content_type": "application/pdf",
                    "expire_seconds": 1800,
                    "content_sha256": SHA_64_B,
                    "ext": "pdf",
                },
            ],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    # 顶层是 batch schema：`items` 字段而非 `tmp_key`
    assert "items" in body, "batch 响应必须含 items 字段"
    assert "tmp_key" not in body, "batch 响应顶层不应出现单文件 tmp_key"
    assert len(body["items"]) == 2
    # 每项是完整 StsTmpKeysResponse
    item_a, item_b = body["items"]
    for item in (item_a, item_b):
        assert "tmp_key" in item
        assert "credentials" in item
        for key in (
            "tmp_secret_id",
            "tmp_secret_key",
            "session_token",
            "start_time",
            "expired_time",
        ):
            assert key in item["credentials"]
        for key in (
            "bucket",
            "region",
            "endpoint",
            "scheme",
            "expires_in",
            "upload_prefix",
        ):
            assert key in item
    # 共享字段（bucket / region / scheme / upload_prefix）跨 item 一致
    assert item_a["bucket"] == item_b["bucket"]
    assert item_a["region"] == item_b["region"]
    assert item_a["scheme"] == item_b["scheme"]
    assert item_a["upload_prefix"] == item_b["upload_prefix"]
    # tmp_key 不同（不同 sha256）
    assert item_a["tmp_key"] != item_b["tmp_key"]
    assert item_a["tmp_key"] == f"tmp/1/{SHA_64_A}.pdf"
    assert item_b["tmp_key"] == f"tmp/1/{SHA_64_B}.pdf"


# ============================================================
# 场景 3：files=[] → 422（schema min_length=1）
# ============================================================
def test_batch_files_empty_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-09-28 review：批量 schema `files=[]` → 422 ValidationError
    （Pydantic min_length=1）。422 响应是 FastAPI 标准结构（顶层 `detail`
    数组），不是后端自定义信封。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={"scope": "parts_new", "files": []},
    )

    assert resp.status_code == 422
    body = resp.json()
    # FastAPI 标准 422 结构：`detail` 数组（不是 `code` / `message` 信封）
    assert "detail" in body
    assert isinstance(body["detail"], list)
    assert len(body["detail"]) > 0
    # 错误应指向 `files` 字段
    locs = [tuple(err.get("loc", [])) for err in body["detail"]]
    assert any("files" in loc for loc in locs), (
        f"422 errors should reference `files`, got {locs}"
    )


# ============================================================
# 场景 4：files 长度=201 → 422（schema max_length=200）
# ============================================================
def test_batch_files_length_201_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-09-28 review：批量 schema `files` 长度=201 → 422
    ValidationError（Pydantic max_length=200）。同样验证 422 响应结构。

    2026-09-29 重构：每项加必填 `content_sha256` + `ext` 才能构造。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "scope": "parts_new",
            "files": [
                {
                    "purpose": "drawing",
                    "filename": f"f{i}.pdf",
                    "content_type": "application/pdf",
                    "content_sha256": ("a" * 60 + f"{i:04d}"),
                    "ext": "pdf",
                }
                for i in range(201)
            ],
        },
    )

    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert isinstance(body["detail"], list)
    locs = [tuple(err.get("loc", [])) for err in body["detail"]]
    assert any("files" in loc for loc in locs), (
        f"422 errors should reference `files`, got {locs}"
    )


# ============================================================
# 附赠：边界 schema（验证 Pydantic v2 smart union 分流真的生效）
# ============================================================
def test_boundary_schema_empty_object_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-28 review 第 1 轮修复：A1 修复连带验收——空对象 `{}`
    必被 smart union + Pydantic 拒为 422，**不会**走到 handler 内的兜底
    `BizError(400)`（422 是 Pydantic 校验阶段的硬错误，handler 进不去）。"""
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post("/api/v1/files/sts-tmp-keys", json={})

    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert isinstance(body["detail"], list)


def test_boundary_schema_purpose_only_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-28 review 第 1 轮修复：A1 修复连带验收——`{"purpose":
    "drawing"}` 缺 `filename` → Pydantic 422（不会走到 handler 兜底
    BizError 400，因 422 在校验阶段先抛）。"""
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={"purpose": "drawing"},
    )

    assert resp.status_code == 422


# ============================================================
# 附赠：2026-09-29 重构相关 schema 校验
# ============================================================
def test_single_file_missing_content_sha256_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29 重构：单文件 schema 缺 `content_sha256` → 422。"""
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "expire_seconds": 1800,
            "ext": "pdf",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    locs = [tuple(err.get("loc", [])) for err in body["detail"]]
    assert any("content_sha256" in loc for loc in locs)


def test_single_file_missing_ext_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29 重构：单文件 schema 缺 `ext` → 422。"""
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "expire_seconds": 1800,
            "content_sha256": SHA_64_A,
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    locs = [tuple(err.get("loc", [])) for err in body["detail"]]
    assert any("ext" in loc for loc in locs)


def test_single_file_ext_uppercase_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29 重构：单文件 schema `ext` 大写 → 422（pattern 强制小写）。"""
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "expire_seconds": 1800,
            "content_sha256": SHA_64_A,
            "ext": "PDF",
        },
    )
    assert resp.status_code == 422


# ============================================================
# 2026-09-29 新增：`X-Forwarded-User-Id` header 端到端覆盖
# ============================================================
def test_endpoint_with_x_forwarded_user_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29：单文件端点带 `X-Forwarded-User-Id: "67890"` →
    响应 `tmp_key` = `tmp/67890/{sha}.pdf`（不走 fallback）。

    端到端验证：header 透传到 service 层 → service 拼 tmp_key 段 → 响应。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        headers={"X-Forwarded-User-Id": "67890"},
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "expire_seconds": 1800,
            "content_sha256": SHA_64_A,
            "ext": "pdf",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["tmp_key"] == f"tmp/67890/{SHA_64_A}.pdf"
    # 反向断言：tmp_key 不含 fallback 段 `tmp/1/`
    assert "tmp/1/" not in body["tmp_key"]


def test_endpoint_without_x_forwarded_user_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29：单文件端点不带 `X-Forwarded-User-Id` header →
    响应 `tmp_key` = `tmp/1/{sha}.pdf`（fallback
    `sts_default_user_id=1`，行为不变）。

    端到端验证：旧链路 A（无 header 的调用方，本端点零 DB IO 也不会受
    影响）继续走 fallback。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "expire_seconds": 1800,
            "content_sha256": SHA_64_A,
            "ext": "pdf",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["tmp_key"] == f"tmp/1/{SHA_64_A}.pdf"


def test_endpoint_with_malformed_user_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29：单文件端点带 `X-Forwarded-User-Id: "abc"`（非 int）
    → 422 FastAPI 标准校验失败。

    FastAPI 按 `int | None` 注解解析 header，非法字符串自动抛
    ValidationError（顶层 `detail` 数组结构）。
    """
    _patch_sdk_with_recorder(monkeypatch)
    client = TestClient(_build_app())

    resp = client.post(
        "/api/v1/files/sts-tmp-keys",
        headers={"X-Forwarded-User-Id": "abc"},
        json={
            "purpose": "drawing",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "expire_seconds": 1800,
            "content_sha256": SHA_64_A,
            "ext": "pdf",
        },
    )

    assert resp.status_code == 422
    body = resp.json()
    # FastAPI 标准 422 结构
    assert "detail" in body
    assert isinstance(body["detail"], list)
    assert len(body["detail"]) > 0
    # 错误应指向 `X-Forwarded-User-Id` header（FastAPI loc 用 alias 字面）
    locs = [tuple(err.get("loc", [])) for err in body["detail"]]
    assert any("X-Forwarded-User-Id" in loc for loc in locs), (
        f"422 errors should reference `X-Forwarded-User-Id`, got {locs}"
    )
