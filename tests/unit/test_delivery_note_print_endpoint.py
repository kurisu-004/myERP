"""送货单 / 标签打印端点的 handler 透传测试（2026-10-04 新增，零 DB）。

为什么单测 handler：`assembly_ids` 的死代码不在 service，而在
`api/v1/delivery_note_print.py` 的两个 handler（此前硬编码
`assembly_ids=None`）。`tests/test_delivery_note_print_service.py` 覆盖不到这层
——它直连 service，传什么就合并什么。因此这里用假 service + dependency_overrides
钉住「body 里的键必须被解析并透传」，否则 service 侧修好了、线上仍然不合并。

覆盖：
- ``/print`` 透传 ``assembly_ids``（str → int）与 ``merge_quantities``；
- ``/print`` 缺省 ``assembly_ids`` → None（不合并，向后兼容）；
- ``/print-labels`` 透传 ``assembly_ids`` + ``line_item_ids``；
- ``assembly_ids`` 含非数字串 → 400 BIZ_INVALID_VALUE（不静默丢弃）。

不挂 DB / docker：handler 只经 ``get_delivery_note_print_service`` 拿 service，
用 ``dependency_overrides`` 换成假实现即可。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_delivery_note_print_service
from api.v1 import delivery_note_print as dn_print
from core.exception_handler import register_exception_handlers

XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class _FakeNotes:
    def __init__(self, note_id: int) -> None:
        self._note_id = note_id

    async def get_by_id(self, note_id: int):
        class _Note:
            id = note_id
            delivery_note_no = "DN-20261004-0001"

        return _Note() if note_id == self._note_id else None


class _FakePrintService:
    """记录 handler 透传给 service 的 kwargs；返回固定 xlsx 字节。"""

    def __init__(self, note_id: int) -> None:
        self.notes = _FakeNotes(note_id)
        self.render_calls: list[dict[str, Any]] = []
        self.render_labels_calls: list[dict[str, Any]] = []

    async def render(self, **kwargs: Any) -> tuple[bytes, str]:
        self.render_calls.append(kwargs)
        return b"fake-xlsx", "F"

    async def render_labels(self, **kwargs: Any) -> tuple[bytes, str]:
        self.render_labels_calls.append(kwargs)
        return b"fake-xlsx", "F"


@pytest.fixture
def fake_service() -> _FakePrintService:
    return _FakePrintService(note_id=1001)


@pytest.fixture
def client(fake_service: _FakePrintService) -> TestClient:
    app = FastAPI()
    # 只挂打印 router + 仓库统一的 BizError → 信封 映射（400/404 断言要用）
    register_exception_handlers(app)
    app.include_router(dn_print.router, prefix="/api/v1")
    app.dependency_overrides[get_delivery_note_print_service] = lambda: fake_service
    return TestClient(app)


def test_print_forwards_assembly_ids_as_int_list(
    client: TestClient, fake_service: _FakePrintService
) -> None:
    """body 的 str 雪花 id 列表 → service 的 int 列表（原硬编码 None 的回归护栏）。"""
    resp = client.post(
        "/api/v1/delivery-notes/1001/print",
        json={
            "merge_assemblies": True,
            "assembly_ids": ["9001", "9002"],
            "merge_quantities": {"9001": 3, "9002": 0},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(XLSX_MIME)
    assert len(fake_service.render_calls) == 1
    call = fake_service.render_calls[0]
    assert call["assembly_ids"] == [9001, 9002]
    assert call["merge_assemblies"] is True
    assert call["merge_quantities"] == {"9001": 3, "9002": 0}


def test_print_defaults_assembly_ids_to_none(
    client: TestClient, fake_service: _FakePrintService
) -> None:
    """body 不带 assembly_ids → 透传 None（不合并，向后兼容旧调用方）。"""
    resp = client.post("/api/v1/delivery-notes/1001/print", json={})
    assert resp.status_code == 200, resp.text
    assert fake_service.render_calls[0]["assembly_ids"] is None


def test_print_labels_forwards_assembly_ids_and_line_item_ids(
    client: TestClient, fake_service: _FakePrintService
) -> None:
    """标签端点同样透传 assembly_ids（否则送货单对了、标签还是「件」）。"""
    resp = client.post(
        "/api/v1/delivery-notes/1001/print-labels",
        json={
            "merge_assemblies": True,
            "assembly_ids": ["9001"],
            "merge_quantities": {"9001": 2},
            "line_item_ids": ["7001", "7002"],
        },
    )
    assert resp.status_code == 200, resp.text
    call = fake_service.render_labels_calls[0]
    assert call["assembly_ids"] == [9001]
    assert call["merge_quantities"] == {"9001": 2}
    assert call["line_item_ids"] == ["7001", "7002"]


def test_print_rejects_non_numeric_assembly_id(
    client: TestClient, fake_service: _FakePrintService
) -> None:
    """非法 id → 400，不静默丢弃（丢弃会退化成「不合并」，打印出与预览不符的单）。"""
    resp = client.post(
        "/api/v1/delivery-notes/1001/print",
        json={"merge_assemblies": True, "assembly_ids": ["not-a-number"]},
    )
    assert resp.status_code == 400, resp.text
    assert fake_service.render_calls == [], "解析失败不应调 service"


def test_print_returns_404_when_note_missing(
    client: TestClient, fake_service: _FakePrintService
) -> None:
    """note 不存在 → 404 BIZ_DELIVERY_NOTE_NOT_FOUND。"""
    resp = client.post("/api/v1/delivery-notes/9999/print", json={})
    assert resp.status_code == 404, resp.text
    assert fake_service.render_calls == []
