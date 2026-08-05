"""C2 + C6 集成测试（PR 2026-08-05）。

C2：装配件参与「下一道工序 / 所在位置 / holder」筛选，通过子件 EXISTS 命中；
    命中装配件携带 matched_children（符合条件的子零件）。
C6：图号 / 名称 keyword 搜装配件（OR 语义，防 AND 回归）。

测试模式参考 tests/test_part_location_tree.py（httpx AsyncClient +
ASGITransport + dependency_overrides，commit 后让请求侧 session 可见）。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from core.permission import CurrentUser, get_current_user
from main import app
from model import (
    TAssembly,
    TCustomer,
    TPart,
    TPartBatch,
    TProcess,
    TShelf,
)
from model.enums import PartLocation, PartStatus, ProcessCategory, ShelfZone

pytestmark = pytest.mark.asyncio


# ============================================================
# Seed helpers
# ============================================================
async def _make_customer(
    session, *, name: str, parent_id: int | None = None, prefix: str | None = None
) -> TCustomer:
    c = TCustomer(name=name, parent_id=parent_id, serial_prefix=prefix)
    session.add(c)
    await session.flush()
    return c


async def _make_process(session, *, code: str, name: str = "工序") -> TProcess:
    p = TProcess(code=code, name=name, category=ProcessCategory.INHOUSE.value)
    session.add(p)
    await session.flush()
    return p


async def _make_shelf(
    session, *, code: str, name: str = "架", zone: str = ShelfZone.PRODUCTION.value
) -> TShelf:
    s = TShelf(code=code, name=name, zone=zone)
    session.add(s)
    await session.flush()
    return s


async def _make_part(
    session,
    *,
    name: str,
    drawing_no: str,
    customer_id: int,
    location: str = PartLocation.OFFICE.value,
    current_holder_id: int | None = None,
    next_process_id: int | None = None,
    assembly_id: int | None = None,
    quantity: int = 1,
) -> TPart:
    """建零件 + 根批次（直接 session.add 绕开 service，必须补根批次）。"""
    status = (
        PartStatus.IN_PROCESS.value
        if location != PartLocation.OFFICE.value
        else PartStatus.PENDING.value
    )
    p = TPart(
        serial_no=None,
        name=name,
        drawing_no=drawing_no,
        applicant_name="申请人",
        quantity=quantity,
        unit_price=0,
        total_price=0,
        request_date=date(2026, 8, 1),
        planned_delivery_date=date(2026, 9, 1),
        is_urgent=False,
        status=status,
        location=location,
        current_holder_id=current_holder_id,
        next_process_id=next_process_id,
        customer_id=customer_id,
        assembly_id=assembly_id,
    )
    session.add(p)
    await session.flush()
    batch = TPartBatch(
        part_id=p.id,
        batch_no=1,
        quantity=p.quantity,
        status=p.status,
        location=p.location,
        current_holder_id=p.current_holder_id,
        next_process_id=p.next_process_id,
    )
    session.add(batch)
    await session.flush()
    return p


async def _make_assembly(
    session, *, name: str, drawing_no: str, customer_id: int, status: str = "PENDING"
) -> TAssembly:
    a = TAssembly(
        serial_no=None,
        name=name,
        drawing_no=drawing_no,
        applicant_name="申请人",
        customer_id=customer_id,
        request_date=date(2026, 8, 1),
        planned_delivery_date=date(2026, 9, 1),
        is_urgent=False,
        status=status,
    )
    session.add(a)
    await session.flush()
    return a


async def _seed_world(session) -> dict[str, Any]:
    """构建最小可区分世界：
    - L1「法拉」(F) → L2「母排厂」
    - 工序 P1 / P2
    - 生产架 X / Y
    - 装配件 A（DWG-A）：子件 A子件 在 X 架，next_process=P1
    - 装配件 A2（DWG-OTHER）：子件 A2子件 在 Y 架，next_process=P2
    - 独立零件 B（DWG-BBB）：在 Y 架，next_process=P2（assembly_id=NULL）
    """
    l1 = await _make_customer(session, name="法拉", prefix="F")
    l2 = await _make_customer(session, name="母排厂", parent_id=l1.id)
    p1 = await _make_process(session, code="P1-C2")
    p2 = await _make_process(session, code="P2-C2")
    shelf_x = await _make_shelf(session, code="SH-X", name="X架")
    shelf_y = await _make_shelf(session, code="SH-Y", name="Y架")

    asm_a = await _make_assembly(
        session, name="装配件A", drawing_no="DWG-A", customer_id=l2.id
    )
    await _make_part(
        session,
        name="A子件",
        drawing_no="DWG-A-1",
        customer_id=l2.id,
        location=PartLocation.PRODUCTION_SHELF.value,
        current_holder_id=shelf_x.id,
        next_process_id=p1.id,
        assembly_id=asm_a.id,
        quantity=3,
    )

    asm_a2 = await _make_assembly(
        session, name="装配件A2", drawing_no="DWG-OTHER", customer_id=l2.id
    )
    await _make_part(
        session,
        name="A2子件",
        drawing_no="DWG-OTHER-1",
        customer_id=l2.id,
        location=PartLocation.PRODUCTION_SHELF.value,
        current_holder_id=shelf_y.id,
        next_process_id=p2.id,
        assembly_id=asm_a2.id,
        quantity=2,
    )

    part_b = await _make_part(
        session,
        name="独立B",
        drawing_no="DWG-BBB",
        customer_id=l2.id,
        location=PartLocation.PRODUCTION_SHELF.value,
        current_holder_id=shelf_y.id,
        next_process_id=p2.id,
    )

    return {
        "l1": l1, "l2": l2, "p1": p1, "p2": p2,
        "shelf_x": shelf_x, "shelf_y": shelf_y,
        "asm_a": asm_a, "asm_a2": asm_a2, "part_b": part_b,
    }


def _fake_mgr() -> Any:
    def _fake() -> CurrentUser:
        return CurrentUser(
            id=1, username="t-mgr", full_name="T", is_active=True,
            roles=("MANAGER",), shelf_ids=(),
        )
    return _fake


async def _get_parts(params: dict[str, Any]) -> dict:
    app.dependency_overrides[get_current_user] = _fake_mgr()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/api/v1/parts", params=params)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ============================================================
# Tests
# ============================================================
class TestAssemblyLocationFilter:
    """C2：装配件参与下一道工序 / 所在位置 / holder 筛选。"""

    async def test_keyword_finds_assembly_by_drawing_no(self, clean_db):
        """C6 回归：图号 keyword 命中装配件（OR 语义，防 AND 回归）。"""
        session = clean_db
        w = await _seed_world(session)
        await session.commit()

        data = await _get_parts({"keyword": "DWG-A", "include_assemblies": "true"})
        top_ids = {it["id"] for it in data["items"]}
        # A 命中；A2（DWG-OTHER）、B（DWG-BBB）、A子件（assembly_id 非空被排除）均不在
        assert str(w["asm_a"].id) in top_ids
        assert str(w["asm_a2"].id) not in top_ids
        assert str(w["part_b"].id) not in top_ids

    async def test_holder_filter_includes_assembly_with_matched_children(self, clean_db):
        """按 holder_ids=[X] 筛选：A 出现且 matched_children 含 A子件；A2 / B 不在。

        用单条件 holder_ids（Part 侧 locations/holder_ids 是 OR 语义，单条件最干净）。
        """
        session = clean_db
        w = await _seed_world(session)
        await session.commit()

        data = await _get_parts({
            "holder_ids": str(w["shelf_x"].id),
            "include_assemblies": "true",
        })
        items = data["items"]
        top_ids = {it["id"] for it in items}

        # A 在顶层（A子件 holder=X 命中但 assembly_id 非空，被 assembly_id_is_null 排除）
        assert str(w["asm_a"].id) in top_ids
        # A2 / B 不在（A2子件 / B 都在 Y 架，不在 [X]）
        assert str(w["asm_a2"].id) not in top_ids
        assert str(w["part_b"].id) not in top_ids

        # A 行携带 matched_children
        asm_item = next(it for it in items if it["id"] == str(w["asm_a"].id))
        mc = asm_item.get("matched_children")
        assert mc, f"expected matched_children, got keys={list(asm_item.keys())}"
        assert len(mc) == 1
        assert mc[0]["drawing_no"] == "DWG-A-1"
        assert mc[0]["row_type"] == "PART"
        # 命中子件本身是装配件子件，无 further children
        assert mc[0]["matched_children"] is None

    async def test_next_process_filter_includes_assembly(self, clean_db):
        """按 next_process_ids=[P1] 筛选：A 出现；A2（子件 P2）/ B（P2）不在。"""
        session = clean_db
        w = await _seed_world(session)
        await session.commit()

        data = await _get_parts({
            "next_process_ids": str(w["p1"].id),
            "include_assemblies": "true",
        })
        top_ids = {it["id"] for it in data["items"]}
        assert str(w["asm_a"].id) in top_ids
        assert str(w["asm_a2"].id) not in top_ids
        assert str(w["part_b"].id) not in top_ids

    async def test_row_type_assembly_only_returns_assemblies(self, clean_db):
        """row_type=ASSEMBLY + holder 筛选：只返回装配件，不返回任何零件行。"""
        session = clean_db
        w = await _seed_world(session)
        await session.commit()

        data = await _get_parts({
            "row_type": "ASSEMBLY",
            "holder_ids": str(w["shelf_x"].id),
            "include_assemblies": "true",
        })
        assert data["items"], "应至少返回装配件 A"
        for it in data["items"]:
            assert it["row_type"] == "ASSEMBLY"
        top_ids = {it["id"] for it in data["items"]}
        assert str(w["asm_a"].id) in top_ids
        assert str(w["asm_a2"].id) not in top_ids
