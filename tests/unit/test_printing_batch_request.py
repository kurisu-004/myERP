"""2026-10-03 新增：`PrintBatchRequest` 契约单测（模型级 + 端点级）。

背景：前端批量打印对「只勾选装配体」的批次发 `part_ids=[]` +
非空 `assembly_ids`，而服务层本就支持纯装配体批量（拉总装图 + 全部子件）。
本文件锁死放宽后的请求契约：

1. `part_ids=["a"]` → 通过（纯零件批，行为不变）；
2. `part_ids=[]` + `assembly_ids=["x"]` → 通过（纯装配体批，本条是本次放宽
   修好的场景；端点级断言 service 收到的 `part_ids=[]` / `assembly_ids=[x]`）；
   `part_ids` 键整个缺省同样走 `default_factory` 落成 `[]` 后通过；
3. `part_ids=[]` + `assembly_ids=None` → 拒绝（`model_validator` 收口「至少一类
   目标非空」，pydantic 包成 `ValidationError`；端点级断言 422 走本仓
   `RequestValidationError` 处理器 = `code=VALIDATION_ERROR` + `data` 错误数组，
   错误项 `loc=["body"]` / `type="value_error"`，不是 FastAPI 默认信封、也不是
   自造结构）；
4. `part_ids` 201 项 → 拒绝（`max_length=200` 批大小硬限；端点级错误项
   `loc=["body","part_ids"]` / `type="too_long"`）。

不依赖 docker / DB / COS：端点级用 `app.dependency_overrides` 注入假 facade，
校验失败路径本就不会进 handler。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.deps import get_printing_service
from api.v1 import printing as printing_api
from api.v1.printing import PrintBatchRequest
from core.error_code import ErrCode
from core.exception_handler import register_exception_handlers


def _build_app() -> FastAPI:
    """最小 app：只挂 printing router（不挂其它 v1 域，免 DB stub）。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(printing_api.router, prefix="/api/v1")
    return app


# ============================================================
# 1：纯零件批通过
# ============================================================
def test_part_ids_only_is_accepted() -> None:
    """`part_ids=["123"]`（`assembly_ids` 缺省）→ 校验通过，字段原样保留。"""
    body = PrintBatchRequest(part_ids=["123"])

    assert body.part_ids == ["123"]
    assert body.assembly_ids is None
    assert body.vector is False


# ============================================================
# 2：纯装配体批通过（本次放宽修好的场景）
# ============================================================
def test_assembly_only_is_accepted() -> None:
    """`part_ids=[]` + `assembly_ids=["123"]` → 校验通过。"""
    body = PrintBatchRequest(part_ids=[], assembly_ids=["123"])

    assert body.part_ids == []
    assert body.assembly_ids == ["123"]


def test_assembly_only_endpoint_reaches_service_with_empty_part_ids() -> None:
    """端点级：纯装配体批穿过 pydantic 后，service 收到 `part_ids=[]` +
    `assembly_ids=[123]`（`vector` 透传），返回 200。"""
    app = _build_app()
    facade = AsyncMock()
    facade.build_parts_print_pdf_batch = AsyncMock(return_value=b"%PDF-1.7 fake")
    app.dependency_overrides[get_printing_service] = lambda: facade
    client = TestClient(app)

    resp = client.post(
        "/api/v1/parts/print-batch",
        json={"part_ids": [], "assembly_ids": ["123"], "vector": True},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    facade.build_parts_print_pdf_batch.assert_awaited_once_with(
        part_ids=[], assembly_ids=[123], vector=True
    )


def test_part_ids_key_absent_defaults_to_empty_list() -> None:
    """`part_ids` 整个缺省 + `assembly_ids=["123"]` → 通过且 `part_ids` 落成
    `[]`（`default_factory=list` 分支，不报 `missing`）。"""
    body = PrintBatchRequest(assembly_ids=["123"])

    assert body.part_ids == []
    assert body.assembly_ids == ["123"]


def test_part_ids_key_absent_endpoint_reaches_service() -> None:
    """端点级：body 里不写 `part_ids` 键 → 200，service 收到 `part_ids=[]`
    + `assembly_ids=[123]`（证明端点级也不依赖调用方显式传空数组）。"""
    app = _build_app()
    facade = AsyncMock()
    facade.build_parts_print_pdf_batch = AsyncMock(return_value=b"%PDF-1.7 fake")
    app.dependency_overrides[get_printing_service] = lambda: facade
    client = TestClient(app)

    resp = client.post(
        "/api/v1/parts/print-batch",
        json={"assembly_ids": ["123"]},
    )

    assert resp.status_code == 200
    facade.build_parts_print_pdf_batch.assert_awaited_once_with(
        part_ids=[], assembly_ids=[123], vector=False
    )


# ============================================================
# 3：两类目标都空 → 拒绝
# ============================================================
def test_both_targets_empty_is_rejected() -> None:
    """`part_ids=[]` + `assembly_ids=None` → `ValidationError`（msg 指向
    「至少提供一个非空列表」）。"""
    with pytest.raises(ValidationError, match="至少提供一个非空列表"):
        PrintBatchRequest(part_ids=[], assembly_ids=None)


def test_both_targets_empty_endpoint_returns_422_envelope() -> None:
    """端点级：空目标 → 422，且走本仓 `RequestValidationError` 处理器
    （`code=VALIDATION_ERROR` + `data` 错误数组），非 FastAPI 默认信封。

    同时锁死错误项的 `loc` / `type`：必须指到 `body` 整体且是 pydantic 模型级
    校验（`value_error`），而不是某个字段的 `missing` / `too_long`——后者意味
    着 `model_validator` 收口被绕过。
    """
    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/parts/print-batch",
        json={"part_ids": [], "assembly_ids": None},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == ErrCode.VALIDATION_ERROR
    assert isinstance(body["data"], list)
    assert len(body["data"]) > 0
    assert "至少提供一个非空列表" in str(body["data"])
    assert body["data"][0]["loc"] == ["body"]
    assert body["data"][0]["type"] == "value_error"


# ============================================================
# 4：批大小硬限（part_ids 201 项）
# ============================================================
def test_part_ids_over_200_is_rejected() -> None:
    """`part_ids` 201 项 → `ValidationError`（`max_length=200`）。

    同时锁住边界：恰好 200 项仍通过（避免把上限写成排他判断）。
    """
    with pytest.raises(ValidationError, match="200"):
        PrintBatchRequest(part_ids=[str(i) for i in range(201)])

    at_limit = PrintBatchRequest(part_ids=[str(i) for i in range(200)])
    assert len(at_limit.part_ids) == 200


def test_part_ids_over_200_endpoint_returns_422() -> None:
    """端点级：`part_ids` 201 项 → 422，错误项 `type=too_long` 且 `loc` 指向
    `["body", "part_ids"]`（字段级超长，而非模型级 `value_error`）。"""
    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/parts/print-batch",
        json={"part_ids": [str(i) for i in range(201)]},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == ErrCode.VALIDATION_ERROR
    assert body["data"][0]["loc"] == ["body", "part_ids"]
    assert body["data"][0]["type"] == "too_long"
