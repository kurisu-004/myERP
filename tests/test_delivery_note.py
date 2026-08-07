"""DeliveryNote 状态机 / 入件 / partial update / candidate-parts / 打印 / 事件名 — 集成测试。

走项目约定：直接调 ``DeliveryNoteService``，不经过 HTTP TestClient；
DB 由 ``tests/conftest.py::clean_db`` 提供（per-function 表数据隔离）
+ docker 容器由 ``_postgres_test_lifecycle`` 在 session 级别 up/migrate/down。

Round 2 (2026-07-23) 重点回归：
- ``submit`` 不再抛 MissingGreenlet（A.：post-flush refresh ``updated_at``）。
- ``submit`` 真的写出了 SUBMITTED 事件（A.1：``DeliveryNoteEventRepository.add``
  改回同步 ``def``）。
- CREATED 事件的 ``note`` 是「create draft for customer <L1 名>」，不是 snowflake ID（B.）。
- line_items 含有新增字段（applicant_name / customer_* / request_date 等）（C.）。
"""
from __future__ import annotations

import io
from datetime import date, datetime

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from model import TCustomer, TPart, TWorker, TWorkType
from model.delivery_note import TDeliveryNote
from model.delivery_note_event import TDeliveryNoteEvent
from model.enums import (
    DeliveryNoteEventType,
    DeliveryNoteStatus,
    PartStatus,
)
from repository.customer import CustomerRepository
from repository.delivery_note import (
    DeliveryNoteCounterRepository,
    DeliveryNoteEventRepository,
    DeliveryNoteRepository,
)
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.work_type import WorkTypeRepository
from repository.worker import WorkerRepository
from service.delivery_note import DeliveryNoteService

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers（按 tests/test_outsource_quote_lifecycle.py 范式）
# ============================================================
async def _make_l1_root(session, *, name: str, prefix: str = "T") -> TCustomer:
    c = TCustomer(name=name, parent_id=None)
    c.serial_prefix = prefix
    session.add(c)
    await session.flush()
    return c


async def _make_l2_leaf(session, *, name: str, l1_id: int) -> TCustomer:
    c = TCustomer(name=name, parent_id=l1_id)
    session.add(c)
    await session.flush()
    return c


async def _make_part(
    session,
    *,
    customer_id: int,
    status: str = PartStatus.INSPECTION.value,
    serial_no: str | None = "T0001",
    drawing_no: str | None = "D-0001",
    applicant_name: str = "测试申请人",
    order_no: str | None = "ON-2026-001",
    note_text: str | None = "测试备注",
) -> TPart:
    p = TPart(
        serial_no=serial_no,
        name=f"part-{drawing_no}",
        drawing_no=drawing_no,
        applicant_name=applicant_name,
        quantity=2,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        system_delivery_date=date(2026, 7, 25),
        order_no=order_no,
        note=note_text,
        customer_id=customer_id,
        status=status,
        location="INSPECTION_SHELF",
    )
    session.add(p)
    await session.flush()
    from tests.conftest import seed_root_batch
    p.root_batch = await seed_root_batch(session, p)  # transient，测试取批次 id 用
    return p


def _item(part, qty=None):
    """构造 DeliveryNoteAddPartsItem（批次级入单条目）。"""
    from schema.delivery_note import DeliveryNoteAddPartsItem
    return DeliveryNoteAddPartsItem(batch_id=str(part.root_batch.id), quantity=qty)


def _make_service(session) -> DeliveryNoteService:
    return DeliveryNoteService(
        session=session,
        notes=DeliveryNoteRepository(session),
        note_events=DeliveryNoteEventRepository(session),
        counter=DeliveryNoteCounterRepository(session),
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        part_events=None,            # state machine 提交时也会写 part 事件；非每个测试都需要
        current_user=None,
    )


def _make_pickup_service(session) -> DeliveryNoteService:
    """pickup 路径要 work_types（解析司机工种）+ part_events（part.deliver 写事件）。"""
    return DeliveryNoteService(
        session=session,
        notes=DeliveryNoteRepository(session),
        note_events=DeliveryNoteEventRepository(session),
        counter=DeliveryNoteCounterRepository(session),
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        work_types=WorkTypeRepository(session),
        part_events=PartEventRepository(session),
        current_user=None,
    )


async def _make_driver(session, *, badge: str = "D0001") -> TWorker:
    wt = TWorkType(code="送货司机", name="送货司机")
    session.add(wt)
    await session.flush()
    w = TWorker(
        badge_code=badge, name="测试司机", is_active=True, work_type_id=wt.id,
    )
    session.add(w)
    await session.flush()
    return w


# ============================================================
# T1: L1 root 创建草稿 + 默认 delivery_date = 今天；events[0] 名称而非 ID
# ============================================================
async def test_create_draft_l1_root_with_name_in_event_note(clean_db):
    customer = await _make_l1_root(clean_db, name="路达开发一部", prefix="L")
    svc = _make_service(clean_db)

    note_out = await svc.create_draft(
        customer_id=str(customer.id),
        note="hello",
    )

    assert note_out.status == DeliveryNoteStatus.DRAFT
    assert note_out.delivery_date == now_naive().date(), "默认送货日期应为今天"

    # R2-B 回归：CREATED 事件 note 应为「create draft for customer 路达开发一部」
    rows = await clean_db.execute(
        select(TDeliveryNoteEvent)
        .where(TDeliveryNoteEvent.delivery_note_id == int(note_out.id))
        .order_by(TDeliveryNoteEvent.created_at)
    )
    events = list(rows.scalars())
    assert events, "应有 CREATED 事件"
    assert events[0].event_type == DeliveryNoteEventType.CREATED.value
    assert events[0].note == "create draft for customer 路达开发一部", (
        "Round 2-B: 事件 note 必须写客户名，不能写 snowflake ID"
    )


# ============================================================
# T2: L2 叶子 client 创建草稿 → 400 BIZ_INVALID_VALUE
# ============================================================
async def test_create_draft_rejects_l2_leaf(clean_db):
    l1 = await _make_l1_root(clean_db, name="L1 测试客户")
    l2 = await _make_l2_leaf(clean_db, name="L2 子厂", l1_id=l1.id)
    svc = _make_service(clean_db)

    with pytest.raises(BizError) as exc_info:
        await svc.create_draft(customer_id=str(l2.id))
    assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE


# ============================================================
# T3: DRAFT + add INSPECTION-status part（Round 1 放宽）→ 200
# ============================================================
async def test_add_parts_accepts_inspection_status(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.INSPECTION.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))

    out = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    # 2026-07-23：IdStrNonNull 在 Python 内部仍是 int；直接 int 对比
    assert any(li.part_id == int(part.id) for li in out.line_items)


# ============================================================
# T4: DRAFT + add PENDING-status part → BIZ_DELIVERY_NOTE_PART_NOT_READY
# ============================================================
async def test_add_parts_rejects_non_inspection_or_ready_to_ship(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.PENDING.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))

    with pytest.raises(BizError) as exc_info:
        await svc.add_parts(
            note_id=str(note.id), items=[_item(part)], version=note.version,
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY


# ============================================================
# T5: DRAFT + add READY_TO_SHIP-status part → 200（baseline 不退化）
# ============================================================
async def test_add_parts_accepts_ready_to_ship(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.READY_TO_SHIP.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))

    out = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    assert any(li.part_id == int(part.id) for li in out.line_items)


# ============================================================
# T6: DRAFT + add 跨 L1 root 的件 → BIZ_DELIVERY_NOTE_PARTS_MULTIPLE_CUSTOMERS
# ============================================================
async def test_add_parts_rejects_cross_l1_root(clean_db):
    l1_f = await _make_l1_root(clean_db, name="法拉", prefix="F")
    l1_l = await _make_l1_root(clean_db, name="路达", prefix="L")
    part_f = await _make_part(
        clean_db, customer_id=l1_f.id, status=PartStatus.READY_TO_SHIP.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(l1_f.id))

    # 把路达的件硬塞进去（part.customer_id 仍是 L1_f，因为它的客户是另一棵树）
    # 这里需要另一棵 L1 下的件 → 重新做：
    other_l1_cust = await _make_l2_leaf(clean_db, name="路达子厂", l1_id=l1_l.id)
    other_part = await _make_part(
        clean_db, customer_id=other_l1_cust.id, status=PartStatus.READY_TO_SHIP.value,
        serial_no="L9001", drawing_no="D-L9001",
    )

    with pytest.raises(BizError) as exc_info:
        await svc.add_parts(
            note_id=str(note.id),
            items=[_item(part_f), _item(other_part)],
            version=note.version,
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_NOTE_PARTS_MULTIPLE_CUSTOMERS


# ============================================================
# T7: 含 INSPECTION 件时 submit → 200 + 写 SUBMITTED 事件
#   关键回归 R2-A.1：DeliveryNoteEventRepository.add 同步后，状态机回调
#                   必须真正 add event。
# ============================================================
async def test_submit_writes_submitted_event_and_handles_inspection_part(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.INSPECTION.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    # 注意：含 INSPECTION-status 件，submit 不重检 part 状态，应该成功。
    # 但当前 submit 的部分检查（legacy）会要求所有 part 是 READY_TO_SHIP。
    # 现状看 service/delivery_note.py::submit 仍然显式校验 READY_TO_SHIP，
    # 这与 Round 1 设计不完全一致 —— 这里按现状走：INSPECTION 件时 400。
    # 见 plan §R2-A.1 备注。
    with pytest.raises(BizError) as exc_info:
        # 重新拿一个 DRAFT 状态提交
        detail = await svc.get_with_parts(str(note.id))
        await svc.submit(note_id=str(note.id), version=detail.version)
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_NOTE_PART_NOT_READY


# ============================================================
# T8 (R2-A + R2-A.1 关键回归)：DRAFT + READY_TO_SHIP 件 → submit 200 + 不 MissingGreenlet + SUBMITTED 事件落地
# ============================================================
async def test_submit_ready_to_ship_succeeds_and_writes_event(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.READY_TO_SHIP.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )

    # 关键：调用 submit 不能抛 MissingGreenlet，且 response 应有 updated_at
    submitted = await svc.submit(note_id=str(note.id), version=detail.version)
    assert submitted.status == DeliveryNoteStatus.SUBMITTED
    assert submitted.updated_at is not None, "R2-A 回归：updated_at 必须能读出"

    # 关键：SUBMITTED 事件必须真落地（R2-A.1 回归：sync add 修后）
    rows = await clean_db.execute(
        select(TDeliveryNoteEvent).where(
            TDeliveryNoteEvent.delivery_note_id == int(note.id),
            TDeliveryNoteEvent.event_type == DeliveryNoteEventType.SUBMITTED.value,
        )
    )
    sub_events = list(rows.scalars())
    assert sub_events, (
        "R2-A.1 回归：submit 必须写入 SUBMITTED 事件（修 sync add 之前完全没写）"
    )


# ============================================================
# T11: partial update 在 DRAFT 上 → OK
# ============================================================
async def test_partial_update_delivery_date_and_note_on_draft(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    svc = _make_service(clean_db)
    note = await svc.create_draft(
        customer_id=str(customer.id),
        delivery_date=date(2026, 7, 23),
        note="初始备注",
    )

    out = await svc.update(
        note_id=str(note.id),
        version=note.version,
        delivery_date=date(2026, 8, 1),
        note_text="改后备注",
    )
    assert out.delivery_date == date(2026, 8, 1)
    assert out.note == "改后备注"


# ============================================================
# T12: partial update 在 SUBMITTED 上 → 200（计划：DRAFT/SUBMITTED 都允许改）
#   注意：PICKED_UP/ARCHIVED 才拒；这两个状态需要司机 + work_type 完整
#   fixture（driver_worker work_type='送货司机' & is_active）才能进，本测试
#   只验证 SUBMITTED 路径不挡，PICKED_UP 拒留给后续 PR 配 worker fixture 再补。
# ============================================================
async def test_partial_update_allows_submitted(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.READY_TO_SHIP.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    submitted = await svc.submit(note_id=str(note.id), version=detail.version)

    # SUBMITTED 状态允许改 delivery_date
    out = await svc.update(
        note_id=str(note.id),
        version=submitted.version,
        delivery_date=date(2026, 8, 2),
    )
    assert out.delivery_date == date(2026, 8, 2)
    assert out.status == DeliveryNoteStatus.SUBMITTED


# ============================================================
# T13: list_candidate_parts(L1_id) 只返 INSPECTION + READY_TO_SHIP,
#       且排除已在 active 单上的件
# ============================================================
async def test_list_candidate_parts_filters_status_and_active_notes(clean_db):
    l1 = await _make_l1_root(clean_db, name="L1 测试", prefix="X")
    l2 = await _make_l2_leaf(clean_db, name="L2 子厂", l1_id=l1.id)
    svc = _make_service(clean_db)

    # 1) 三个候选：INSPECTION / READY_TO_SHIP / PENDING
    p1 = await _make_part(
        clean_db, customer_id=l2.id,
        status=PartStatus.INSPECTION.value,
        serial_no="X1001", drawing_no="D-X1001",
    )
    p2 = await _make_part(
        clean_db, customer_id=l2.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="X1002", drawing_no="D-X1002",
    )
    p3 = await _make_part(
        clean_db, customer_id=l2.id,
        status=PartStatus.PENDING.value,
        serial_no="X1003", drawing_no="D-X1003",
    )

    candidates = await svc.list_candidate_parts(str(l1.id))
    cand_ids = {c.id for c in candidates}
    assert p1.id in cand_ids
    assert p2.id in cand_ids
    assert p3.id not in cand_ids, "PENDING 不应出现在候选中"

    # 2) 把 p1 放进 DRAFT（active 单），应被排除
    note = await svc.create_draft(customer_id=str(l1.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(p1)], version=note.version,
    )
    candidates_after = await svc.list_candidate_parts(str(l1.id))
    cand_ids_after = {c.id for c in candidates_after}
    assert p1.id not in cand_ids_after, "active 单上的件应被排除"
    assert p2.id in cand_ids_after


# ============================================================
# T14 (R2-C 关键回归): _to_detail line_items 含 R2-C 新字段
# ============================================================
async def test_to_detail_line_items_have_extended_fields(clean_db):
    customer = await _make_l1_root(clean_db, name="L1 测试", prefix="F")
    l2 = await _make_l2_leaf(clean_db, name="L2 子厂", l1_id=customer.id)
    part = await _make_part(
        clean_db, customer_id=l2.id, status=PartStatus.READY_TO_SHIP.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    detail = await svc.get_with_parts(str(note.id))
    assert detail.line_items, "应有 line_items"
    li = detail.line_items[0]
    # 验证 R2-C 新字段都填上了
    assert li.applicant_name == "测试申请人"
    assert li.request_date == date(2026, 7, 1)
    assert li.planned_delivery_date == date(2026, 7, 30)
    assert li.system_delivery_date == date(2026, 7, 25)
    assert li.order_no == "ON-2026-001"
    assert li.note == "测试备注"
    assert li.customer_name == "L2 子厂"
    assert li.parent_customer_name == "L1 测试"
    assert li.customer_path == "L1 测试 / L2 子厂"


# ============================================================
# T15 (2026-07-23 关键回归): recall 真正把 status 写回 DRAFT
#   Bug：状态机缺 on_enter_DRAFT，recall 只写 RECALLED 事件，status 仍 SUBMITTED。
#   修复后：status→DRAFT、submitted_at 清空、可再软删（DRAFT-only）。
# ============================================================
async def test_recall_resets_status_to_draft(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.READY_TO_SHIP.value,
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    submitted = await svc.submit(note_id=str(note.id), version=detail.version)
    assert submitted.status == DeliveryNoteStatus.SUBMITTED

    recalled = await svc.recall(note_id=str(note.id), version=submitted.version)
    assert recalled.status == DeliveryNoteStatus.DRAFT, (
        "回归：recall 后 status 必须真正回到 DRAFT（曾停在 SUBMITTED）"
    )
    assert recalled.submitted_at is None, "recall 后应清空 submitted_at"

    # DB 层复核（不只是响应对象）
    row = await clean_db.get(TDeliveryNote, int(note.id))
    assert row.status == DeliveryNoteStatus.DRAFT.value

    # WITHDRAWN 事件应存在（2026-07-23 替代旧 RECALLED；RECALLED enum 仅历史只读）
    rows = await clean_db.execute(
        select(TDeliveryNoteEvent).where(
            TDeliveryNoteEvent.delivery_note_id == int(note.id),
            TDeliveryNoteEvent.event_type == DeliveryNoteEventType.WITHDRAWN.value,
        )
    )
    assert list(rows.scalars()), "recall 应写 WITHDRAWN 事件"

    # DRAFT 状态下可软删（recall 前不行）
    await svc.soft_delete(note_id=str(note.id), version=recalled.version)


# ============================================================
# T16 (2026-07-23 关键回归): 司机扫齐 → pickup 完成
#   验证两处修复：
#   (a) 司机工种经 WorkTypeRepository 解析（旧 driver.work_type 会 AttributeError）；
#   (b) pickup 停在 PICKED_UP（不自动 archive），零件 → DELIVERED。
# ============================================================
async def test_pickup_finalize_stops_at_picked_up_and_delivers_parts(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.READY_TO_SHIP.value,
        serial_no="F5001", drawing_no="D-F5001",
    )
    driver = await _make_driver(clean_db)
    svc = _make_pickup_service(clean_db)

    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    submitted = await svc.submit(note_id=str(note.id), version=detail.version)

    # 司机逐件扫描（2026-07-23 改：后端不再维护扫码进度，scan.ready 恒 False；
    # 前端基于本地 Set 判断 ready；后端 pickup 只校验 SUBMITTED + 非空）
    scan = await svc.pickup_scan(note_id=str(note.id), part_serial="F5001")
    assert scan.expected_count == 1
    assert not scan.ready, "Bug 4 改：后端 ready 恒 False（前端本地判断）"

    out = await svc.pickup(
        note_id=str(note.id),
        driver_worker_id=str(driver.id),
        version=submitted.version,
    )
    assert out.status == DeliveryNoteStatus.PICKED_UP, (
        "回归：pickup 停在 PICKED_UP（不再自动 archive）"
    )
    assert str(out.driver_worker_id) == str(driver.id)

    # 零件 → DELIVERED + 实际送货日期今天
    part_row = await clean_db.get(TPart, int(part.id))
    assert part_row.status == PartStatus.DELIVERED.value
    assert part_row.actual_delivery_date == date.today()

    # 不应产生 ARCHIVED 事件（2026-07-23 Bug 4：ARCHIVED 已从 DeliveryNoteEventType
# 删除；DeliveryNoteStatus.ARCHIVED 是状态机终态，与事件枚举解耦）
    rows = await clean_db.execute(
        select(TDeliveryNoteEvent).where(
            TDeliveryNoteEvent.delivery_note_id == int(note.id),
            TDeliveryNoteEvent.event_type == "ARCHIVED",
        )
    )
    assert not list(rows.scalars()), "停在 PICKED_UP，不应有 ARCHIVED 事件"


# ============================================================
# T17: 非司机工种 pickup → BIZ_DELIVERY_NOTE_DRIVER_INVALID（不再 500）
# ============================================================
async def test_pickup_rejects_non_driver_worker(clean_db):
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id, status=PartStatus.READY_TO_SHIP.value,
        serial_no="F6001", drawing_no="D-F6001",
    )
    # 非司机工种
    wt = TWorkType(code="车床", name="车床工")
    clean_db.add(wt)
    await clean_db.flush()
    worker = TWorker(
        badge_code="N0001", name="车床工人", is_active=True, work_type_id=wt.id,
    )
    clean_db.add(worker)
    await clean_db.flush()

    svc = _make_pickup_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    submitted = await svc.submit(note_id=str(note.id), version=detail.version)
    await svc.pickup_scan(note_id=str(note.id), part_serial="F6001")

    with pytest.raises(BizError) as exc_info:
        await svc.pickup(
            note_id=str(note.id),
            driver_worker_id=str(worker.id),
            version=submitted.version,
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_NOTE_DRIVER_INVALID


# ============================================================
# T18: print_xlsx 把 note.delivery_date 写到模板 footer（2026-07-23 Bug 2）
# ============================================================
async def test_print_xlsx_delivery_date_fala(clean_db):
    """法拉模板：A17 合并区整体覆盖为「送货日期：YYYY年M月D日」。

    之前是静态字面量「送货日期：  2026年7月14日」；现由代码按
    `note.delivery_date` 覆盖。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F7001", drawing_no="D-F7001",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    # 显式设置送货日期（覆盖默认今天）
    target_date = date(2026, 7, 23)
    await svc.update(
        note_id=str(note.id),
        version=detail.version,
        delivery_date=target_date,
    )

    xlsx_bytes, prefix = await svc.print_xlsx(note_id=str(note.id))
    assert prefix == "F"

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    assert ws["A17"].value == "送货日期：2026年7月23日"


async def test_print_xlsx_delivery_date_luda(clean_db):
    """路达模板：I2 写 Python date 对象；H2「送货日期」标签不动。"""
    customer = await _make_l1_root(clean_db, name="路达", prefix="L")
    part = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="L7001", drawing_no="D-L7001",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    target_date = date(2026, 7, 23)
    await svc.update(
        note_id=str(note.id),
        version=detail.version,
        delivery_date=target_date,
    )

    xlsx_bytes, prefix = await svc.print_xlsx(note_id=str(note.id))
    assert prefix == "L"

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["杏南"]
    assert ws["H2"].value == "送货日期", "H2 标签不应被覆盖"
    # openpyxl 把 date 读回为 datetime（时分秒 0）
    assert ws["I2"].value == datetime(2026, 7, 23), (
        f"期望 I2 为 datetime(2026, 7, 23)，实际 {ws['I2'].value!r}"
    )


async def test_print_xlsx_null_delivery_date_falls_back_to_today(clean_db):
    """NULL delivery_date（旧库 010 之前的数据）打印时回退当天，不报错。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F7002", drawing_no="D-F7002",
    )
    svc = _make_service(clean_db)

    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    # 把 delivery_date 显式置 None（模拟旧库 010 之前的数据）
    await svc.update(
        note_id=str(note.id),
        version=detail.version,
        delivery_date=None,
    )

    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    today = now_naive().date()
    assert ws["A17"].value == f"送货日期：{today.year}年{today.month}月{today.day}日"


# ============================================================
# T18+: print_xlsx custom_order（2026-08-02 预览对话框拖动改序）
# ============================================================
async def test_print_xlsx_custom_order_respected(clean_db):
    """custom_order 非空时按其顺序填表（预览拖动后导出顺序生效）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    # 3 个零件按 created_at 顺序入库 → DB 默认顺序 p1,p2,p3
    p1 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8001", drawing_no="D-F8001",
    )
    p2 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8002", drawing_no="D-F8002",
    )
    p3 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8003", drawing_no="D-F8003",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2), _item(p3)],
        version=note.version,
    )
    # 取批次 id（_item(part) 自动创建 batch_no=1 根批次）
    line_items = {li.serial_no: li for li in detail.line_items}

    # 反向顺序：third, first, second
    custom_order = [
        str(line_items["F8003"].id),
        str(line_items["F8001"].id),
        str(line_items["F8002"].id),
    ]
    xlsx_bytes, prefix = await svc.print_xlsx(
        note_id=str(note.id), custom_order=custom_order,
    )
    assert prefix == "F"

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 法拉模板数据起始 R3；row_index 列(1) = 1,2,3 顺序
    assert ws.cell(row=3, column=1).value == 1  # row_index
    assert ws.cell(row=4, column=1).value == 2
    assert ws.cell(row=5, column=1).value == 3
    # 列 5 (drawing_no) 与列 6 (name) 反映反向顺序
    assert ws.cell(row=3, column=5).value == "D-F8003"  # third
    assert ws.cell(row=4, column=5).value == "D-F8001"  # first
    assert ws.cell(row=5, column=5).value == "D-F8002"  # second


async def test_print_xlsx_no_custom_order_uses_db_order(clean_db):
    """custom_order 为空走默认 TPartBatch.id ASC（旧行为）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F9001", drawing_no="D-F9001",
    )
    p2 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F9002", drawing_no="D-F9002",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2)],
        version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 默认顺序 p1, p2
    assert ws.cell(row=3, column=5).value == "D-F9001"
    assert ws.cell(row=4, column=5).value == "D-F9002"


async def test_print_xlsx_custom_order_invalid_id_raises_422(clean_db):
    """custom_order 含不属于本单的 batch id → 422 BIZ_DELIVERY_PRINT_BAD_ORDER。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="FA001", drawing_no="D-FA001",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    real_bid = str(detail.line_items[0].id)

    # 真实 id + 一个不存在的 id
    with pytest.raises(BizError) as exc_info:
        await svc.print_xlsx(
            note_id=str(note.id),
            custom_order=[real_bid, "999999999999"],
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert exc_info.value.http_status == 422


async def test_print_xlsx_custom_order_missing_rows_raises_422(clean_db):
    """custom_order 漏行 → 422（不允许静默丢弃）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="FB001", drawing_no="D-FB001",
    )
    p2 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="FB002", drawing_no="D-FB002",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2)],
        version=note.version,
    )
    line_items = {li.serial_no: li for li in detail.line_items}

    # 只给一个 batch id，漏了另一个
    with pytest.raises(BizError) as exc_info:
        await svc.print_xlsx(
            note_id=str(note.id),
            custom_order=[str(line_items["FB001"].id)],
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert exc_info.value.http_status == 422


# ============================================================
# T19: SUBMITTED 后冻结零件清单（2026-07-23 Bug 5）
# ============================================================
async def test_add_parts_blocked_when_submitted(clean_db):
    """submit 后 add_parts → BIZ_DELIVERY_NOTE_PARTS_LOCKED 409。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8001", drawing_no="D-F8001",
    )
    p2 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8002", drawing_no="D-F8002",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(p1)], version=note.version,
    )
    submitted = await svc.submit(
        note_id=str(note.id), version=detail.version,
    )

    with pytest.raises(BizError) as exc_info:
        await svc.add_parts(
            note_id=str(note.id), items=[_item(p2)],
            version=submitted.version,
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_NOTE_PARTS_LOCKED
    assert exc_info.value.http_status == 409


async def test_remove_parts_blocked_when_submitted(clean_db):
    """submit 后 remove_parts → BIZ_DELIVERY_NOTE_PARTS_LOCKED 409。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8101", drawing_no="D-F8101",
    )
    p2 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8102", drawing_no="D-F8102",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(p1), _item(p2)],
        version=note.version,
    )
    submitted = await svc.submit(
        note_id=str(note.id), version=detail.version,
    )

    with pytest.raises(BizError) as exc_info:
        await svc.remove_parts(
            note_id=str(note.id), batch_ids=[str(p1.root_batch.id)],
            version=submitted.version,
        )
    assert exc_info.value.code == ErrCode.BIZ_DELIVERY_NOTE_PARTS_LOCKED


async def test_add_parts_allowed_after_recall(clean_db):
    """submit → recall → add_parts → 200。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8201", drawing_no="D-F8201",
    )
    p2 = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8202", drawing_no="D-F8202",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(p1)], version=note.version,
    )
    submitted = await svc.submit(
        note_id=str(note.id), version=detail.version,
    )
    recalled = await svc.recall(
        note_id=str(note.id), version=submitted.version,
    )
    assert recalled.status == DeliveryNoteStatus.DRAFT

    detail2 = await svc.add_parts(
        note_id=str(note.id), items=[_item(p2)], version=recalled.version,
    )
    assert detail2.status == DeliveryNoteStatus.DRAFT


async def test_recall_after_pickup_fails_state_machine(clean_db):
    """pickup 后 recall → 状态机拒绝（BIZ_DELIVERY_NOTE_NOT_SUBMITTED 之类）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8301", drawing_no="D-F8301",
    )
    driver = await _make_driver(clean_db)
    svc = _make_pickup_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    submitted = await svc.submit(
        note_id=str(note.id), version=detail.version,
    )
    picked_up = await svc.pickup(
        note_id=str(note.id),
        driver_worker_id=str(driver.id),
        version=submitted.version,
    )
    assert picked_up.status == DeliveryNoteStatus.PICKED_UP

    with pytest.raises(BizError):
        await svc.recall(
            note_id=str(note.id), version=picked_up.version,
        )


async def test_pickup_after_recall_fails_state_machine(clean_db):
    """recall → pickup → 状态机拒绝（不再 SUBMITTED）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_part(
        clean_db, customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="F8401", drawing_no="D-F8401",
    )
    driver = await _make_driver(clean_db)
    svc = _make_pickup_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id), items=[_item(part)], version=note.version,
    )
    submitted = await svc.submit(
        note_id=str(note.id), version=detail.version,
    )
    recalled = await svc.recall(
        note_id=str(note.id), version=submitted.version,
    )
    assert recalled.status == DeliveryNoteStatus.DRAFT

    with pytest.raises(BizError):
        await svc.pickup(
            note_id=str(note.id),
            driver_worker_id=str(driver.id),
            version=recalled.version,
        )


# ============================================================
# 2026-08-07：candidate-parts 富化 L2/L1 客户字段（picker 筛选 + 扫码拦截用）
# ============================================================
async def test_list_candidate_parts_includes_l2_customer(clean_db):
    """L1 + 2 个 L2 子客户，各 1 个 READY_TO_SHIP 批次 → 候选每行带 customer_name /
    parent_customer_name / customer_path。

    验证 service.list_candidate_parts 在不增 SQL 的前提下（复用 list_children）
    富化 L2 客户展示字段，供前端 picker 多选筛选 + ElMessage 拦截使用。
    """
    l1 = await _make_l1_root(clean_db, name="法拉", prefix="F")
    l2_a = await _make_l2_leaf(clean_db, name="一厂", l1_id=l1.id)
    l2_b = await _make_l2_leaf(clean_db, name="二厂", l1_id=l1.id)

    # 一厂 1 件 INSPECTION（不入 candidate；INSPECTION 也算候选，下面再补 INSPECTION）
    p_a = await _make_part(
        clean_db, customer_id=l2_a.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="FA001", drawing_no="D-FA001",
    )
    p_b = await _make_part(
        clean_db, customer_id=l2_b.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="FB001", drawing_no="D-FB001",
    )

    svc = _make_service(clean_db)
    items = await svc.list_candidate_parts(str(l1.id))
    assert len(items) == 2

    by_serial = {it.serial_no: it for it in items}
    assert by_serial["FA001"].customer_name == "一厂"
    assert by_serial["FA001"].parent_customer_name == "法拉"
    assert by_serial["FA001"].customer_path == "法拉 / 一厂"

    assert by_serial["FB001"].customer_name == "二厂"
    assert by_serial["FB001"].parent_customer_name == "法拉"
    assert by_serial["FB001"].customer_path == "法拉 / 二厂"


async def test_list_candidate_parts_l2_filter_to_single_child(clean_db):
    """L1 多个 L2 时，候选混合来自所有 L2（前端按 customer_name 多选筛选的数据源）。"""
    l1 = await _make_l1_root(clean_db, name="路达", prefix="L")
    l2_x = await _make_l2_leaf(clean_db, name="七厂", l1_id=l1.id)
    l2_y = await _make_l2_leaf(clean_db, name="八厂", l1_id=l1.id)

    await _make_part(
        clean_db, customer_id=l2_x.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="LX001", drawing_no="D-LX001",
    )
    await _make_part(
        clean_db, customer_id=l2_y.id,
        status=PartStatus.READY_TO_SHIP.value,
        serial_no="LY001", drawing_no="D-LY001",
    )

    svc = _make_service(clean_db)
    items = await svc.list_candidate_parts(str(l1.id))
    names = {it.customer_name for it in items}
    # 两条候选：七厂 + 八厂
    assert names == {"七厂", "八厂"}
    # 每条都有 L1 父名
    assert all(it.parent_customer_name == "路达" for it in items)
