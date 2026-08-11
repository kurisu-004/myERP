"""采购订单 Excel 导入集成测试（2026-08-11）。

覆盖 `PartService.batch_match_by_excel_items` 与
`PartService.batch_update_order_info` 两个新方法 + 两个对应 endpoint。

走真实 DB（tests/conftest.py::clean_db + docker 容器）。
API 层测试用 httpx + ASGITransport + dependency_overrides（参考
`tests/test_part_filter_null_predicates.py`）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from pydantic import ValidationError
from sqlalchemy import select

from core.error_code import ErrCode
from core.permission import CurrentUser, get_current_user
from main import app
from model import (
    TAssembly,
    TCustomer,
    TPart,
    TPartBatch,
)
from model.enums import PartLocation, PartStatus
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from schema.part import (
    PartBatchOrderInfoMatchItem,
    PartBatchOrderInfoMatchRequest,
    PartBatchOrderInfoUpdateItem,
    PartBatchOrderInfoUpdateRequest,
)
from service.part import PartService

pytestmark = pytest.mark.asyncio


# ============================================================
# Seed helpers
# ============================================================
async def _make_customer(session, *, name: str, prefix: str = "P") -> TCustomer:
    c = TCustomer(name=name, parent_id=None, serial_prefix=prefix)
    session.add(c)
    await session.flush()
    return c


async def _make_part(
    session,
    *,
    name: str,
    drawing_no: str,
    customer_id: int,
    assembly_id: int | None = None,
    quantity: int = 100,
    unit_price: Decimal | None = None,
    order_no: str | None = None,
    system_delivery_date: date | None = None,
    is_soft_deleted: bool = False,
) -> TPart:
    """建零件 + 根批次（直接 session.add 绕开 service，必须补根批次）。"""
    p = TPart(
        serial_no=None,
        name=name,
        drawing_no=drawing_no,
        applicant_name="申请人",
        quantity=quantity,
        unit_price=unit_price,
        total_price=(unit_price or Decimal(0)) * quantity,
        order_no=order_no,
        system_delivery_date=system_delivery_date,
        request_date=date(2026, 8, 1),
        planned_delivery_date=date(2026, 9, 1),
        is_urgent=False,
        status=PartStatus.PENDING.value,
        location=PartLocation.OFFICE.value,
        current_holder_id=None,
        next_process_id=None,
        customer_id=customer_id,
        assembly_id=assembly_id,
    )
    session.add(p)
    await session.flush()
    if is_soft_deleted:
        # 用 PartRepository.soft_delete 走 deleted_at 路径（避免直接改字段绕过审计）
        await PartRepository(session).soft_delete(p)
        await session.flush()
    else:
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
    session, *, name: str, drawing_no: str, customer_id: int,
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
        status=PartStatus.PENDING.value,
    )
    session.add(a)
    await session.flush()
    return a


def _make_service(session) -> PartService:
    """构造带全部 repo 的 PartService（batch_match/update 不强求所有 repo，
    但补全避免 _to_out 等辅助方法报 None 错误）。"""
    return PartService(
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        events=PartEventRepository(session),
        serial_counters=SerialCounterRepository(session),
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        work_types=WorkTypeRepository(session),
        work_type_process=WorkTypeProcessRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
        assemblies=AssemblyRepository(session),
    )


def _fake_user(roles: tuple[str, ...] = ("MANAGER",)) -> Any:
    """返回 `_fake` 工厂；用于 dependency_overrides。"""
    def _fake() -> CurrentUser:
        return CurrentUser(
            id=1, username="t-user", full_name="T", is_active=True,
            roles=roles, shelf_ids=(),
        )
    return _fake


async def _get_part(session, part_id: int) -> TPart:
    row = await session.execute(
        select(TPart).where(TPart.id == part_id, TPart.deleted_at.is_(None))
    )
    return row.scalar_one()


# ============================================================
# 1. Match: 图号精确命中 Part
# ============================================================
async def test_match_part_code_exact_hit(clean_db):
    """图号精确命中 Part → match_type=PART_CODE, parts=[1], warnings=[]。"""
    cust = await _make_customer(clean_db)
    await _make_part(
        clean_db, name="A件", drawing_no="DWG-A1",
        customer_id=cust.id, quantity=100, unit_price=Decimal(10),
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-001",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no="DWG-A1", name="A件",
                quantity=Decimal(100), unit_price=Decimal(10),
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)

    assert len(results) == 1
    r = results[0]
    assert r.match_type == "PART_CODE"
    assert len(r.parts) == 1
    assert r.parts[0].drawing_no == "DWG-A1"
    assert r.warnings == []


# ============================================================
# 2. Match: 名称归一化命中（全角括号 → 半角）
# ============================================================
async def test_match_part_name_normalized(clean_db):
    """名称归一化：全角括号 → 半角，命中已有 Part → PART_NAME。"""
    cust = await _make_customer(clean_db)
    # 库存里就用「半角 + 无空格」的归一化形式
    await _make_part(
        clean_db, name="垫片(公制)", drawing_no="DWG-NM",
        customer_id=cust.id, quantity=50,
    )
    svc = _make_service(clean_db)

    # 输入：全角括号 + 多余空格
    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-002",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no=None, name="  垫片（公制）  ",
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "PART_NAME"
    assert len(r.parts) == 1
    assert r.parts[0].name == "垫片(公制)"


# ============================================================
# 3. Match: 完全无命中
# ============================================================
async def test_match_none(clean_db):
    """图号 / 名称都不命中 → NONE, parts=[]。"""
    cust = await _make_customer(clean_db)
    await _make_part(
        clean_db, name="X", drawing_no="DWG-X",
        customer_id=cust.id,
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-003",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no="DWG-NONE", name="不存在",
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "NONE"
    assert r.parts == []
    assert r.warnings == []


# ============================================================
# 4. Match: 同图号多 Part → warning
# ============================================================
async def test_match_multiple_parts_same_code_warning(clean_db):
    """同图号 2 个 Part → match_type=PART_CODE, parts=2, warning「命中 N 条」。"""
    cust = await _make_customer(clean_db)
    await _make_part(
        clean_db, name="A1", drawing_no="DWG-DUP",
        customer_id=cust.id,
    )
    await _make_part(
        clean_db, name="A2", drawing_no="DWG-DUP",
        customer_id=cust.id,
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-004",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no="DWG-DUP", name="A1",
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "PART_CODE"
    assert len(r.parts) == 2
    assert any("命中 2 条" in w for w in r.warnings), r.warnings


# ============================================================
# 5. Match: 图号命中 Assembly → 展开 active 子件（软删过滤）
# ============================================================
async def test_match_assembly_code_expands_active_children(clean_db):
    """图号命中 Assembly → ASSEMBLY_CODE, parts=子件列表（软删子件不返）。"""
    cust = await _make_customer(clean_db)
    asm = await _make_assembly(
        clean_db, name="总装A", drawing_no="DWG-ASM-A",
        customer_id=cust.id,
    )
    await _make_part(
        clean_db, name="子1", drawing_no="DWG-ASM-A-C1",
        customer_id=cust.id, assembly_id=asm.id,
    )
    await _make_part(
        clean_db, name="子2", drawing_no="DWG-ASM-A-C2",
        customer_id=cust.id, assembly_id=asm.id,
    )
    # 软删一个子件 → 应被 list_children 过滤
    await _make_part(
        clean_db, name="子3软删", drawing_no="DWG-ASM-A-C3",
        customer_id=cust.id, assembly_id=asm.id, is_soft_deleted=True,
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-005",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no="DWG-ASM-A", name="总装A",
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "ASSEMBLY_CODE"
    assert len(r.parts) == 2
    names = {p.name for p in r.parts}
    assert names == {"子1", "子2"}
    assert all(p.assembly_id == asm.id for p in r.parts)


# ============================================================
# 6. Match: 图号同时命中 Part + Assembly → 取 Part + warning
# ============================================================
async def test_match_part_and_assembly_same_code(clean_db):
    """图号同时命中 Part + Assembly → match_type=PART_CODE, parts=[Part], warning 含
    「同时命中装配体」。"""
    cust = await _make_customer(clean_db)
    await _make_assembly(
        clean_db, name="总装X", drawing_no="DWG-DUAL",
        customer_id=cust.id,
    )
    part = await _make_part(
        clean_db, name="零件X", drawing_no="DWG-DUAL",
        customer_id=cust.id,
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-006",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no="DWG-DUAL", name="零件X",
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "PART_CODE"
    assert len(r.parts) == 1
    assert r.parts[0].part_id == str(part.id)
    assert any("同时命中装配体" in w for w in r.warnings), r.warnings


# ============================================================
# 7. Match: 单价 / 数量超容差 → warning（仍匹配成功）
# ============================================================
async def test_match_price_qty_tolerance_warning(clean_db):
    """单价 100 vs 输入 200 → 偏差超 1% 容差 → warning 包含「含税价偏差」；
    匹配结果仍 PART_CODE 成功。"""
    cust = await _make_customer(clean_db)
    await _make_part(
        clean_db, name="B件", drawing_no="DWG-B",
        customer_id=cust.id, quantity=100, unit_price=Decimal(100),
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoMatchRequest(
        doc_no="PO-007",
        items=[
            PartBatchOrderInfoMatchItem(
                row_no=2, drawing_no="DWG-B", name="B件",
                unit_price=Decimal(200),  # 100% 偏差，肯定超容差
                quantity=Decimal(500),    # 400% 偏差，肯定超容差
            ),
        ],
    )
    results = await svc.batch_match_by_excel_items(payload)
    assert len(results) == 1
    r = results[0]
    assert r.match_type == "PART_CODE"
    assert len(r.parts) == 1
    warnings_text = " ".join(r.warnings)
    assert "含税价" in warnings_text and "偏差超容差" in warnings_text, r.warnings
    assert "可出货数量" in warnings_text and "偏差超容差" in warnings_text, r.warnings


# ============================================================
# 8. Match: 空 items 列表（min_length=1 违反）→ Pydantic ValidationError
# ============================================================
async def test_match_empty_items_pydantic_validation_error():
    """items=[] 违反 min_length=1 → Pydantic ValidationError（不是 BizError）。"""
    with pytest.raises(ValidationError):
        PartBatchOrderInfoMatchRequest(doc_no="PO-008", items=[])


# ============================================================
# 9. Update: Happy path（3 条全成功，version+1，DB 落库）
# ============================================================
async def test_update_happy_path(clean_db):
    """3 条全部成功 → updated=3, DB 中 order_no/system_delivery_date 正确，version+1。"""
    cust = await _make_customer(clean_db)
    p1 = await _make_part(clean_db, name="U1", drawing_no="DWG-U1", customer_id=cust.id)
    p2 = await _make_part(clean_db, name="U2", drawing_no="DWG-U2", customer_id=cust.id)
    p3 = await _make_part(clean_db, name="U3", drawing_no="DWG-U3", customer_id=cust.id)
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p1.id), version=p1.version,
                order_no="PO-U1", system_delivery_date=date(2026, 10, 1),
            ),
            PartBatchOrderInfoUpdateItem(
                part_id=str(p2.id), version=p2.version,
                order_no="PO-U2", system_delivery_date=date(2026, 10, 2),
            ),
            PartBatchOrderInfoUpdateItem(
                part_id=str(p3.id), version=p3.version,
                order_no="PO-U3", system_delivery_date=date(2026, 10, 3),
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert len(result.updated) == 3
    assert result.failed == []
    assert result.skipped_count == 0

    # DB 验证
    refreshed = await _get_part(clean_db, p1.id)
    assert refreshed.order_no == "PO-U1"
    assert refreshed.system_delivery_date == date(2026, 10, 1)
    assert refreshed.version == p1.version + 1


# ============================================================
# 10. Update: 部分成功（1 valid + 1 stale + 1 not-found）
# ============================================================
async def test_update_partial_success(clean_db):
    """1 有效 + 1 version 过期（40901） + 1 part_id 不存在（20101）。
    updated=1, failed=2。"""
    cust = await _make_customer(clean_db)
    p_ok = await _make_part(clean_db, name="OK", drawing_no="DWG-OK", customer_id=cust.id)
    p_stale = await _make_part(clean_db, name="ST", drawing_no="DWG-ST", customer_id=cust.id)
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p_ok.id), version=p_ok.version,
                order_no="PO-OK",
            ),
            PartBatchOrderInfoUpdateItem(
                part_id=str(p_stale.id), version=p_stale.version + 99,  # 错的 version
                order_no="PO-ST",
            ),
            PartBatchOrderInfoUpdateItem(
                # 999999999999999999 远超雪花 ID；service parse_snowflake_id
                # 成功但 get_by_id 返回 None → BIZ_PART_NOT_FOUND
                part_id="999999999999999999", version=0,
                order_no="PO-NF",
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert len(result.updated) == 1
    assert len(result.failed) == 2
    # 失败码：版本冲突 40901；不存在 20101
    codes = sorted(f.code for f in result.failed)
    assert codes == sorted([ErrCode.BIZ_VERSION_CONFLICT, ErrCode.BIZ_PART_NOT_FOUND])

    # 有效那条确实落库
    refreshed = await _get_part(clean_db, p_ok.id)
    assert refreshed.order_no == "PO-OK"
    assert refreshed.version == p_ok.version + 1
    # 过期那条不变
    stale = await _get_part(clean_db, p_stale.id)
    assert stale.order_no is None
    assert stale.version == p_stale.version


# ============================================================
# 11. Update: 全部失败
# ============================================================
async def test_update_all_failed(clean_db):
    """3 条全部失败 → updated=[], failed=3, DB 零改动。"""
    cust = await _make_customer(clean_db)
    p = await _make_part(clean_db, name="F", drawing_no="DWG-F", customer_id=cust.id)
    svc = _make_service(clean_db)
    original_version = p.version

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p.id), version=p.version + 1,  # stale
                order_no="PO-F-1",
            ),
            PartBatchOrderInfoUpdateItem(
                part_id="999999999999999998", version=0,
                order_no="PO-F-2",
            ),
            PartBatchOrderInfoUpdateItem(
                part_id="999999999999999997", version=0,
                order_no="PO-F-3",
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert result.updated == []
    assert len(result.failed) == 3
    # DB 零改动
    refreshed = await _get_part(clean_db, p.id)
    assert refreshed.order_no is None
    assert refreshed.version == original_version


# ============================================================
# 12. Update: skip=True → 不写库，skipped_count 正确
# ============================================================
async def test_update_skip_flag(clean_db):
    """skip=True 的条目不写 DB，skipped_count 正确计数。"""
    cust = await _make_customer(clean_db)
    p1 = await _make_part(clean_db, name="S1", drawing_no="DWG-S1", customer_id=cust.id)
    p2 = await _make_part(clean_db, name="S2", drawing_no="DWG-S2", customer_id=cust.id)
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p1.id), version=p1.version,
                order_no="PO-S1", skip=True,
            ),
            PartBatchOrderInfoUpdateItem(
                part_id=str(p2.id), version=p2.version,
                order_no="PO-S2", skip=False,
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert result.skipped_count == 1
    # 只有 p2 更新成功
    assert len(result.updated) == 1
    assert result.failed == []
    s1 = await _get_part(clean_db, p1.id)
    s2 = await _get_part(clean_db, p2.id)
    assert s1.order_no is None
    assert s1.version == p1.version
    assert s2.order_no == "PO-S2"
    assert s2.version == p2.version + 1


# ============================================================
# 13. Update: order_no="" → 清空语义（DB order_no = NULL）
# ============================================================
async def test_update_order_no_empty_clears(clean_db):
    """order_no="" → service strip 后 or None → DB order_no = NULL（清空）。"""
    cust = await _make_customer(clean_db)
    p = await _make_part(
        clean_db, name="CL", drawing_no="DWG-CL", customer_id=cust.id,
        order_no="PO-OLD",
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p.id), version=p.version, order_no="",
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert len(result.updated) == 1
    refreshed = await _get_part(clean_db, p.id)
    assert refreshed.order_no is None
    assert refreshed.version == p.version + 1


# ============================================================
# 14. Update: system_delivery_date=None → 清空语义
# ============================================================
async def test_update_system_delivery_date_none_clears(clean_db):
    """system_delivery_date=None → DB 列被显式置 NULL（清空）。"""
    cust = await _make_customer(clean_db)
    p = await _make_part(
        clean_db, name="SD", drawing_no="DWG-SD", customer_id=cust.id,
        system_delivery_date=date(2026, 12, 1),
    )
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p.id), version=p.version,
                system_delivery_date=None,
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert len(result.updated) == 1
    refreshed = await _get_part(clean_db, p.id)
    assert refreshed.system_delivery_date is None
    assert refreshed.version == p.version + 1


# ============================================================
# 15. Update: 重复 part_id → 第一条成功，第二条记 40901，不报 500
# ============================================================
async def test_update_duplicate_part_id(clean_db):
    """同一 part_id 出现两次 → 第一条成功，第二条 failure（40901）。"""
    cust = await _make_customer(clean_db)
    p = await _make_part(clean_db, name="DUP", drawing_no="DWG-DUP", customer_id=cust.id)
    svc = _make_service(clean_db)

    payload = PartBatchOrderInfoUpdateRequest(
        items=[
            PartBatchOrderInfoUpdateItem(
                part_id=str(p.id), version=p.version, order_no="PO-FIRST",
            ),
            PartBatchOrderInfoUpdateItem(
                part_id=str(p.id), version=p.version, order_no="PO-SECOND",
            ),
        ],
    )
    result = await svc.batch_update_order_info(payload)
    await clean_db.commit()

    assert len(result.updated) == 1
    assert len(result.failed) == 1
    assert result.failed[0].code == ErrCode.BIZ_VERSION_CONFLICT
    refreshed = await _get_part(clean_db, p.id)
    assert refreshed.order_no == "PO-FIRST"  # 首条胜出


# ============================================================
# 16. API 层：CNC_PROGRAMMER 角色（非 office）→ HTTP 403
# ============================================================
async def test_api_batch_update_order_info_rejects_non_office_role(clean_db):
    """API 层 _office_dep = MANAGER/CLERK/CNC_PROGRAMMER；用 SHELF_ACCOUNT（非 office）
    角色访问 → HTTP 403。"""
    cust = await _make_customer(clean_db)
    p = await _make_part(clean_db, name="R", drawing_no="DWG-R", customer_id=cust.id)
    await clean_db.commit()

    # 用 SHELF_ACCOUNT 角色（不在 _office_dep 的 3 角色内）触发 403
    app.dependency_overrides[get_current_user] = _fake_user(("SHELF_ACCOUNT",))
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            resp = await client.post(
                "/api/v1/parts/batch-update-order-info",
                json={
                    "items": [{
                        "part_id": str(p.id), "version": p.version,
                        "order_no": "PO-R",
                    }],
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] != 0  # 非 ok 信封
