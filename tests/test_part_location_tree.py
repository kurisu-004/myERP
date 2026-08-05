"""GET /parts/location-tree 端点回归测试（PR-C1 2026-08-05）。

修复前：`api/v1/part.py` 的 `get_parts_location_tree` 在模块级用
`PartLocation` 但顶部只 import 了 `PartEventType / ShelfZone / UserRole`，
触发 `NameError: PartLocation not defined`。修复方法：把缺失的
`PartLocation / PartSortKey / PartStatus / SortDir` 上提到顶部 import，
并删除函数体内的局部 import。

本测试直接通过 httpx.AsyncClient + ASGITransport 调
`/api/v1/parts/location-tree`（在 pytest 当前 event loop 内运行，
避免 TestClient 跨线程的 asyncpg loop 冲突），覆盖：
- 200 + 信封 `code==0`
- `data.items` 长度 5（OFFICE / PRODUCTION_SHELF / WORKER /
  INSPECTION_SHELF / OUTSOURCE_COMPANY）
- 含 `id == "OFFICE"` 的节点（直接验证 NameError 已修）
- PRODUCTION_SHELF 节点有 children（seed 插入的 PRODUCTION 架）
"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from core.permission import CurrentUser, get_current_user
from main import app
from model import TOutsourceCompany, TShelf, TWorker
from model.enums import ShelfZone

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers
# ============================================================
async def _seed_for_location_tree(session) -> dict:
    """Seed 1 个 PRODUCTION 架 + 1 个 INSPECTION 架 + 1 个 active 工人
    + 1 个 active 外协公司。返回 dict 供后续断言。
    """
    prod = TShelf(code="LT-PROD-1", name="生产架1", zone=ShelfZone.PRODUCTION.value)
    insp = TShelf(code="LT-INSP-1", name="品检架1", zone=ShelfZone.INSPECTION.value)
    worker = TWorker(badge_code="LT-W-1", name="LT工1", is_active=True)
    company = TOutsourceCompany(name="LT外协公司1", is_active=True)
    session.add_all([prod, insp, worker, company])
    await session.flush()
    return {"prod": prod, "insp": insp, "worker": worker, "company": company}


# ============================================================
# Test
# ============================================================
class TestPartsLocationTreeEndpoint:
    """GET /parts/location-tree — 父节点 = PartLocation 大类（5 个）。"""

    async def test_parts_location_tree_endpoint(self, clean_db):
        session = clean_db
        await _seed_for_location_tree(session)
        # httpx.AsyncClient 走 main.app → 新 session 通过 api.deps.get_session
        # 读已 commit 的数据；先 commit 让另一 session 看到。
        await session.commit()

        # 用 MANAGER 角色 bypass auth（_read_part_dep 接受 MANAGER/CLERK/
        # CNC_PROGRAMMER/INSPECTOR）。
        async def _fake_current_user() -> CurrentUser:
            return CurrentUser(
                id=1,
                username="test-mgr",
                full_name="测试管理员",
                is_active=True,
                roles=("MANAGER",),
                shelf_ids=(),
            )

        app.dependency_overrides[get_current_user] = _fake_current_user
        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver",
            ) as client:
                resp = await client.get("/api/v1/parts/location-tree")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0, body
        items = body["data"]["items"]
        assert len(items) == 5, items

        # 节点 id 用 PartLocation.value 字符串；验证 NameError 已修
        ids = {item["id"] for item in items}
        assert ids == {
            "OFFICE",
            "PRODUCTION_SHELF",
            "WORKER",
            "INSPECTION_SHELF",
            "OUTSOURCE_COMPANY",
        }

        # PRODUCTION_SHELF 节点有 children（seed 时插入 1 个生产架）
        by_id = {item["id"]: item for item in items}
        prod_node = by_id["PRODUCTION_SHELF"]
        assert len(prod_node["children"]) == 1
        # 子节点 location 字段为 None（叶子）
        assert prod_node["children"][0]["location"] is None
        # 子节点 name 由 shelf.code 派生；有 name 时格式 = "{code} {name}"
        assert prod_node["children"][0]["name"] == "LT-PROD-1 生产架1"
