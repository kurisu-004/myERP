"""`/api/mcp/*` 只读查询端点 + MCP tool 白名单回归测试（2026-08-08 新增）。

这组端点有三个与 `/api/v1/*` 截然不同的性质，每一个都必须锁死：

1. **免鉴权**——不带 Authorization 也要 200（且**不**设 `dependency_overrides`，
   否则测的就不是「真的不需要登录」了）。
2. **裸 JSON**——`UnifiedResponseMiddleware` 对 `/api/mcp` 豁免，顶层直接是业务对象，
   没有 `{code, message, data}` 信封。
3. **只有白名单端点变成 MCP tool**——`core/mcp_server.py` 的 route_maps 少写一条
   终止 EXCLUDE，全站 150 个端点就会变成免鉴权 MCP tool。

另外 `/api/mcp/files/{id}/content` 是免鉴权的二进制出口，只放行 `kind=DRAWING`，
否则遍历 file_id 就能拿到 G 代码 / 3D 模型。
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from httpx import ASGITransport

from main import app
from model import (
    TAssembly,
    TCustomer,
    TPart,
    TPartFile,
    TShelf,
    TWorker,
)
from model.enums import PartFileKind, PartStatus, ShelfZone
from tests.conftest import seed_root_batch

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 8, 8)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)

DUE_URL = "/api/mcp/parts/due"


# ============================================================
# Helpers
# ============================================================
async def _client_get(url: str, **params):
    """不带任何 Authorization 头请求——免鉴权是被测行为本身。"""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as client:
        return await client.get(url, params=params or None)


def _mk_part(
    *,
    name: str,
    serial_no: str,
    customer_id: int | None = None,
    system_delivery_date: date | None = TODAY,
    status: str = PartStatus.IN_PROCESS.value,
    location: str | None = "PRODUCTION_SHELF",
    holder_id: int | None = None,
    assembly_id: int | None = None,
    is_urgent: bool = False,
    quantity: int = 10,
) -> TPart:
    return TPart(
        serial_no=serial_no,
        name=name,
        drawing_no=f"DWG-{serial_no}",
        applicant_name="张申请",
        quantity=quantity,
        request_date=YESTERDAY,
        planned_delivery_date=TOMORROW,
        system_delivery_date=system_delivery_date,
        status=status,
        location=location,
        current_holder_id=holder_id,
        customer_id=customer_id,
        assembly_id=assembly_id,
        is_urgent=is_urgent,
    )


async def _seed_basic(session) -> dict:
    """1 个货架 + 1 个工人 + 父/子两级客户。"""
    shelf = TShelf(code="MCP-S1", name="MCP架1", zone=ShelfZone.PRODUCTION.value)
    worker = TWorker(badge_code="MCP-W1", name="MCP工人甲", is_active=True)
    parent = TCustomer(name="法拉电子", serial_prefix="F")
    session.add_all([shelf, worker, parent])
    await session.flush()
    child = TCustomer(name="母排厂", parent_id=parent.id)
    session.add(child)
    await session.flush()
    return {"shelf": shelf, "worker": worker, "parent": parent, "child": child}


# ============================================================
# 1. 免鉴权 + 裸 JSON
# ============================================================
class TestMcpDueContract:
    async def test_no_auth_required_and_no_envelope(self, clean_db):
        """无 Authorization 头照样 200，且响应**不带**统一信封。"""
        session = clean_db
        env = await _seed_basic(session)
        part = _mk_part(
            name="免鉴权件", serial_no="F0001",
            customer_id=env["child"].id, holder_id=env["shelf"].id,
        )
        session.add(part)
        await session.flush()
        await seed_root_batch(session, part)
        await session.commit()

        resp = await _client_get(DUE_URL, date=TODAY.isoformat())

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 裸 JSON：顶层直接是业务对象，没有 code/message/data 包装。
        assert "code" not in body
        assert "message" not in body
        assert "data" not in body
        assert body["total"] == 1
        assert body["items"][0]["serial_no"] == "F0001"

    async def test_v1_still_requires_auth_and_keeps_envelope(self, clean_db):
        """对照组：/api/v1 的行为**不能**被这次改动带偏。"""
        await clean_db.commit()
        resp = await _client_get("/api/v1/parts")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] != 0  # 仍是信封结构


# ============================================================
# 2. 日期与状态筛选语义
# ============================================================
class TestMcpDueFiltering:
    async def test_null_system_delivery_date_excluded(self, clean_db):
        """系统交期为 NULL = 未排期，不算「到期」，必须排除。

        这条最容易回归：repository 的 `system_delivery_date_to` 本身是
        NULL-inclusive 的，全靠 `system_delivery_date_not_null` 开关兜着。
        """
        session = clean_db
        env = await _seed_basic(session)
        dated = _mk_part(
            name="有交期", serial_no="F0001",
            customer_id=env["child"].id, system_delivery_date=TODAY,
        )
        undated = _mk_part(
            name="无交期", serial_no="F0002",
            customer_id=env["child"].id, system_delivery_date=None,
        )
        session.add_all([dated, undated])
        await session.flush()
        for p in (dated, undated):
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        serials = {i["serial_no"] for i in body["items"]}
        assert serials == {"F0001"}
        assert body["total"] == 1

    async def test_overdue_included_future_excluded(self, clean_db):
        """`<=` 语义：逾期的要出现，未到期的不出现。"""
        session = clean_db
        env = await _seed_basic(session)
        overdue = _mk_part(
            name="逾期件", serial_no="F0001",
            customer_id=env["child"].id,
            system_delivery_date=TODAY - timedelta(days=30),
        )
        today_due = _mk_part(
            name="今天到期", serial_no="F0002",
            customer_id=env["child"].id, system_delivery_date=TODAY,
        )
        future = _mk_part(
            name="还没到期", serial_no="F0003",
            customer_id=env["child"].id, system_delivery_date=TOMORROW,
        )
        session.add_all([overdue, today_due, future])
        await session.flush()
        for p in (overdue, today_due, future):
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        assert {i["serial_no"] for i in body["items"]} == {"F0001", "F0002"}

    async def test_delivered_completed_cancelled_excluded(self, clean_db):
        """「未送货」= 排除 DELIVERED / COMPLETED / CANCELLED。"""
        session = clean_db
        env = await _seed_basic(session)
        parts = [
            _mk_part(
                name=f"件-{st}", serial_no=f"F000{i}",
                customer_id=env["child"].id, status=st,
            )
            for i, st in enumerate(
                [
                    PartStatus.IN_PROCESS.value,
                    PartStatus.READY_TO_SHIP.value,
                    PartStatus.DELIVERED.value,
                    PartStatus.COMPLETED.value,
                    PartStatus.CANCELLED.value,
                ],
                start=1,
            )
        ]
        session.add_all(parts)
        await session.flush()
        for p in parts:
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        assert {i["serial_no"] for i in body["items"]} == {"F0001", "F0002"}
        assert body["total"] == 2

    async def test_statuses_whitelist_intersects_not_overrides(self, clean_db):
        """传 statuses 只能**缩小**范围，不能拿 DELIVERED 绕过「未送货」语义。"""
        session = clean_db
        env = await _seed_basic(session)
        in_process = _mk_part(
            name="加工中", serial_no="F0001",
            customer_id=env["child"].id, status=PartStatus.IN_PROCESS.value,
        )
        ready = _mk_part(
            name="待发货", serial_no="F0002",
            customer_id=env["child"].id, status=PartStatus.READY_TO_SHIP.value,
        )
        delivered = _mk_part(
            name="已送达", serial_no="F0003",
            customer_id=env["child"].id, status=PartStatus.DELIVERED.value,
        )
        session.add_all([in_process, ready, delivered])
        await session.flush()
        for p in (in_process, ready, delivered):
            await seed_root_batch(session, p)
        await session.commit()

        # 缩小到 READY_TO_SHIP 生效
        body = (await _client_get(
            DUE_URL, date=TODAY.isoformat(), statuses="READY_TO_SHIP",
        )).json()
        assert {i["serial_no"] for i in body["items"]} == {"F0002"}

        # 只传已送货状态 → 400，而不是静默返回空列表
        # （空列表会让 AI 误以为「确实没有到期未送货的件」）
        resp = await _client_get(
            DUE_URL, date=TODAY.isoformat(), statuses="DELIVERED",
        )
        assert resp.status_code == 400

        # 拼错 / 不识别 → 400，**不**静默丢弃后用剩下的过滤。
        # AI 把状态拼成 `delivered`（小写）或加一个无效值，200 拿到错误
        # 过滤结果却毫无察觉，比空结果还糟糕。
        bad = await _client_get(
            DUE_URL, date=TODAY.isoformat(),
            statuses=["READY_TO_SHIP", "delivered"],
        )
        assert bad.status_code == 400
        assert "delivered" in bad.json()["message"]

    async def test_customer_name_matches_parent_and_descendants(self, clean_db):
        """传父客户名要能捞到子厂的零件。"""
        session = clean_db
        env = await _seed_basic(session)
        other = TCustomer(name="路达科技", serial_prefix="L")
        session.add(other)
        await session.flush()

        mine = _mk_part(
            name="法拉的件", serial_no="F0001", customer_id=env["child"].id,
        )
        theirs = _mk_part(
            name="路达的件", serial_no="L0001", customer_id=other.id,
        )
        session.add_all([mine, theirs])
        await session.flush()
        for p in (mine, theirs):
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(
            DUE_URL, date=TODAY.isoformat(), customer_name="法拉",
        )).json()

        assert {i["serial_no"] for i in body["items"]} == {"F0001"}
        assert body["items"][0]["customer_path"] == "法拉电子 / 母排厂"

        # 匹配不到任何客户 → 空结果，而不是退化成全量查询
        empty = (await _client_get(
            DUE_URL, date=TODAY.isoformat(), customer_name="不存在的客户",
        )).json()
        assert empty["total"] == 0

    async def test_is_urgent_filter(self, clean_db):
        session = clean_db
        env = await _seed_basic(session)
        urgent = _mk_part(
            name="加急件", serial_no="F0001",
            customer_id=env["child"].id, is_urgent=True,
        )
        normal = _mk_part(
            name="普通件", serial_no="F0002",
            customer_id=env["child"].id, is_urgent=False,
        )
        session.add_all([urgent, normal])
        await session.flush()
        for p in (urgent, normal):
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(
            DUE_URL, date=TODAY.isoformat(), is_urgent="true",
        )).json()
        assert {i["serial_no"] for i in body["items"]} == {"F0001"}


# ============================================================
# 3. 批次 / 装配体 / 图纸的结构
# ============================================================
class TestMcpDueShape:
    async def test_multi_batch_locations_are_all_reported(self, clean_db):
        """多批次分散在不同位置时，`batches` 要能同时给出货架和工人两处。"""
        from model import TPartBatch

        session = clean_db
        env = await _seed_basic(session)
        part = _mk_part(
            name="多批次件", serial_no="F0001",
            customer_id=env["child"].id, quantity=100,
            holder_id=env["shelf"].id,
        )
        session.add(part)
        await session.flush()
        session.add_all([
            TPartBatch(
                part_id=part.id, batch_no=1, quantity=60,
                status=PartStatus.IN_PROCESS.value,
                location="PRODUCTION_SHELF", current_holder_id=env["shelf"].id,
            ),
            TPartBatch(
                part_id=part.id, batch_no=2, quantity=40,
                status=PartStatus.IN_PROCESS.value,
                location="WORKER", current_holder_id=env["worker"].id,
            ),
        ])
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        row = body["items"][0]
        assert row["quantity"] == 100  # 工单总量
        batches = {b["batch_no"]: b for b in row["batches"]}
        assert len(batches) == 2
        assert batches[1]["quantity"] == 60
        assert batches[1]["holder_display"] == "货架 MCP-S1"
        assert batches[2]["quantity"] == 40
        assert batches[2]["holder_display"] == "工人 MCP工人甲"
        assert batches[1]["batch_label"] == "F0001B1"

    async def test_terminal_batches_hidden_in_due_list(self, clean_db):
        """列表里隐藏 COMPLETED / CANCELLED 批次（它们对「在哪」没有信息量）。"""
        from model import TPartBatch

        session = clean_db
        env = await _seed_basic(session)
        part = _mk_part(
            name="含终态批次", serial_no="F0001",
            customer_id=env["child"].id, quantity=100,
            holder_id=env["shelf"].id,
        )
        session.add(part)
        await session.flush()
        session.add_all([
            TPartBatch(
                part_id=part.id, batch_no=1, quantity=70,
                status=PartStatus.IN_PROCESS.value,
                location="PRODUCTION_SHELF", current_holder_id=env["shelf"].id,
            ),
            TPartBatch(
                part_id=part.id, batch_no=2, quantity=30,
                status=PartStatus.CANCELLED.value, location=None,
            ),
        ])
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()
        assert [b["batch_no"] for b in body["items"][0]["batches"]] == [1]

    async def test_assembly_children_are_grouped(self, clean_db):
        """命中的子件聚成 ASSEMBLY 行；`total` 数零件，`items` 数行。"""
        session = clean_db
        env = await _seed_basic(session)
        asm = TAssembly(
            serial_no="F0100", drawing_no="DWG-ASM", name="某装配体",
            customer_id=env["child"].id,
            request_date=YESTERDAY, planned_delivery_date=TOMORROW,
            status="IN_PROCESS",
        )
        session.add(asm)
        await session.flush()

        child1 = _mk_part(
            name="子件1", serial_no="F0100-01",
            customer_id=env["child"].id, assembly_id=asm.id,
        )
        child2 = _mk_part(
            name="子件2", serial_no="F0100-02",
            customer_id=env["child"].id, assembly_id=asm.id,
        )
        # 同装配体下但未到期的子件 —— 不该出现在 children 里
        child3 = _mk_part(
            name="子件3", serial_no="F0100-03",
            customer_id=env["child"].id, assembly_id=asm.id,
            system_delivery_date=TOMORROW,
        )
        standalone = _mk_part(
            name="独立件", serial_no="F0200", customer_id=env["child"].id,
        )
        session.add_all([child1, child2, child3, standalone])
        await session.flush()
        for p in (child1, child2, child3, standalone):
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        # 命中 3 个零件（2 子件 + 1 独立），但只有 2 行（装配体聚合 + 独立件）
        assert body["total"] == 3
        assert len(body["items"]) == 2

        asm_row = next(i for i in body["items"] if i["row_type"] == "ASSEMBLY")
        assert asm_row["serial_no"] == "F0100"
        assert asm_row["name"] == "某装配体"
        # 装配体自己没有系统交期，业务字段为空
        assert asm_row["system_delivery_date"] is None
        assert asm_row["quantity"] is None
        # children 只含**命中**的子件，不含 F0100-03
        assert {c["serial_no"] for c in asm_row["children"]} == {
            "F0100-01", "F0100-02",
        }

        part_row = next(i for i in body["items"] if i["row_type"] == "PART")
        assert part_row["serial_no"] == "F0200"
        assert part_row["children"] == []

    async def test_drawing_reference_and_download_path(self, clean_db):
        """只返回 kind=DRAWING，且给出可直接 GET 的 download_path。"""
        session = clean_db
        env = await _seed_basic(session)
        part = _mk_part(
            name="有图纸的件", serial_no="F0001", customer_id=env["child"].id,
        )
        session.add(part)
        await session.flush()
        await seed_root_batch(session, part)
        drawing = TPartFile(
            part_id=part.id, kind=PartFileKind.DRAWING.value,
            file_type="pdf", object_key="k/drawing.pdf",
            original_filename="图纸.pdf", file_size=1234,
            content_type="application/pdf", upload_status="SUCCESS",
        )
        gcode = TPartFile(
            part_id=part.id, kind=PartFileKind.G_CODE.value,
            file_type="nc", object_key="k/prog.nc",
            original_filename="prog.nc", file_size=99,
            content_type="text/plain", upload_status="SUCCESS",
        )
        session.add_all([drawing, gcode])
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        d = body["items"][0]["drawing"]
        assert d["filename"] == "图纸.pdf"
        assert d["content_type"] == "application/pdf"
        # 雪花 ID 序列化为字符串（防 JS 精度丢失）
        assert isinstance(d["file_id"], str)
        assert d["download_path"] == f"/api/mcp/files/{d['file_id']}/content"

    async def test_pagination_reports_total_over_items(self, clean_db):
        session = clean_db
        env = await _seed_basic(session)
        parts = [
            _mk_part(
                name=f"件{i}", serial_no=f"F{i:04d}", customer_id=env["child"].id,
            )
            for i in range(1, 6)
        ]
        session.add_all(parts)
        await session.flush()
        for p in parts:
            await seed_root_batch(session, p)
        await session.commit()

        body = (await _client_get(
            DUE_URL, date=TODAY.isoformat(), limit=2, offset=0,
        )).json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert len(body["items"]) == 2

        # limit 上限 500
        assert (await _client_get(
            DUE_URL, date=TODAY.isoformat(), limit=501,
        )).status_code == 422


# ============================================================
# 4. by-serial 详情
# ============================================================
class TestMcpBySerial:
    async def test_returns_all_batches_including_terminal(self, clean_db):
        from model import TPartBatch

        session = clean_db
        env = await _seed_basic(session)
        part = _mk_part(
            name="详情件", serial_no="F0001",
            customer_id=env["child"].id, quantity=100,
            holder_id=env["shelf"].id,
        )
        session.add(part)
        await session.flush()
        session.add_all([
            TPartBatch(
                part_id=part.id, batch_no=1, quantity=70,
                status=PartStatus.IN_PROCESS.value,
                location="PRODUCTION_SHELF", current_holder_id=env["shelf"].id,
            ),
            TPartBatch(
                part_id=part.id, batch_no=2, quantity=30,
                status=PartStatus.CANCELLED.value, location=None,
            ),
        ])
        await session.commit()

        resp = await _client_get("/api/mcp/parts/by-serial/F0001")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" not in body  # 同样是裸 JSON
        assert body["name"] == "详情件"
        # 详情给全部批次，含终态
        assert {b["batch_no"] for b in body["all_batches"]} == {1, 2}
        # batch_label 要和列表接口一致（曾漏传 serial_no 导致详情里全是 null）
        assert {b["batch_label"] for b in body["all_batches"]} == {
            "F0001B1", "F0001B2",
        }

    async def test_unknown_serial_returns_404(self, clean_db):
        await clean_db.commit()
        resp = await _client_get("/api/mcp/parts/by-serial/NOPE-9999")
        assert resp.status_code == 404


# ============================================================
# 4b. 客户路径：三层客户树不能被截断
# ============================================================
class TestMcpCustomerPathDepth:
    async def test_three_level_customer_path_is_complete(self, clean_db):
        """客户树加到三层时，`customer_path` 必须含全部祖先。

        手写「取一轮 parent」的预取会在第三层把路径截断成「二厂 / 冲压车间」；
        共享的 `preload_customer_cache` 按层 BFS 直到载完，才拿得到完整路径。
        """
        session = clean_db
        root = TCustomer(name="法拉电子", serial_prefix="F")
        session.add(root)
        await session.flush()
        mid = TCustomer(name="二厂", parent_id=root.id)
        session.add(mid)
        await session.flush()
        leaf = TCustomer(name="冲压车间", parent_id=mid.id)
        session.add(leaf)
        await session.flush()

        part = _mk_part(name="三层客户件", serial_no="F0001", customer_id=leaf.id)
        session.add(part)
        await session.flush()
        await seed_root_batch(session, part)
        await session.commit()

        body = (await _client_get(DUE_URL, date=TODAY.isoformat())).json()

        assert body["items"][0]["customer_path"] == "法拉电子 / 二厂 / 冲压车间"


# ============================================================
# 5. 图纸代理的安全闸
# ============================================================
class TestMcpDrawingProxy:
    async def _seed_file(self, session, kind: str, key: str) -> TPartFile:
        env = await _seed_basic(session)
        part = _mk_part(
            name="件", serial_no="F0001", customer_id=env["child"].id,
        )
        session.add(part)
        await session.flush()
        await seed_root_batch(session, part)
        f = TPartFile(
            part_id=part.id, kind=kind, file_type="pdf", object_key=key,
            original_filename="f.pdf", file_size=3,
            content_type="application/pdf", upload_status="SUCCESS",
        )
        session.add(f)
        await session.commit()
        return f

    async def test_drawing_content_served_without_auth(self, clean_db, fake_cos):
        f = await self._seed_file(
            clean_db, PartFileKind.DRAWING.value, "k/ok.pdf",
        )
        fake_cos.objects["k/ok.pdf"] = b"PDF"

        resp = await _client_get(f"/api/mcp/files/{f.id}/content")

        assert resp.status_code == 200
        assert resp.content == b"PDF"
        assert resp.headers["content-type"].startswith("application/pdf")

    async def test_non_drawing_kinds_are_404(self, clean_db, fake_cos):
        """🔒 免鉴权出口只放行 DRAWING：G 代码等不能靠遍历 file_id 拿到。"""
        f = await self._seed_file(
            clean_db, PartFileKind.G_CODE.value, "k/secret.nc",
        )
        fake_cos.objects["k/secret.nc"] = b"G0 X0"

        resp = await _client_get(f"/api/mcp/files/{f.id}/content")

        assert resp.status_code == 404
        # 未下载 COS：安全闸在查 DB 之后、下载之前就拦住了
        assert "k/secret.nc" not in fake_cos.download_calls


# ============================================================
# 6. MCP tool 白名单（最关键的一条）
# ============================================================
class TestMcpToolWhitelist:
    async def test_only_whitelisted_endpoints_become_mcp_tools(self):
        """route_maps 少一条终止 EXCLUDE，全站端点就会变成免鉴权 MCP tool。

        这条断言故意写死 tool 名集合：新增 MCP 端点时必须显式改这里，
        避免「不小心多暴露了一个」悄无声息地溜过去。
        """
        from fastmcp import FastMCP

        from core.mcp_server import _ROUTE_MAPS

        mcp = FastMCP.from_fastapi(
            app=app, name="myERP-test", route_maps=_ROUTE_MAPS,
        )
        tools = await mcp.list_tools()

        assert {t.name for t in tools} == {
            "query_parts_due",
            "get_part_by_serial",
        }
        # 二进制端点不做 tool
        assert "get_drawing_content" not in {t.name for t in tools}
        # response_model → outputSchema，AI 靠它解释返回值
        for t in tools:
            assert t.output_schema, f"{t.name} 缺少 outputSchema"
            assert t.description, f"{t.name} 缺少 description"
