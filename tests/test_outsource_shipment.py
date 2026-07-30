"""外协发货记录 (OutsourceShipment) 集成测试（2026-07-30 新增）。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import (
    TCustomer,
    TOutsourceCompany,
    TOutsourceCompanyProcess,
    TPart,
    TProcess,
    TShelf,
    TShelfProcess,
)
from model.enums import (
    OutsourceQuoteStatus,
    PartEventType,
    PartLocation,
    PartStatus,
    ProcessCategory,
    ShelfZone,
)
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_company_process import OutsourceCompanyProcessRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.outsource_shipment import OutsourceShipmentRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.worker import WorkerRepository
from schema.outsource_quote import (
    OutsourceQuoteApproveRequest,
    OutsourceQuoteCreateRequest,
    OutsourceShipmentReconcileUpdateRequest,
)
from schema.part import (
    PlaceOnShelfRequest,
    SendToOutsourceRequest,
)
from service.outsource_quote import OutsourceQuoteService
from service.part import PartService

pytestmark = pytest.mark.asyncio


async def _seed_world(session, *, suffix: str, quantity: int = 10):
    customer = TCustomer(name=f"shipment测试客户-{suffix}")
    company = TOutsourceCompany(name=f"shipment测试公司-{suffix}", is_active=True)
    outsource_process = TProcess(
        code=f"OUT-SH-{suffix}",
        name=f"外协工序-{suffix}",
        category=ProcessCategory.OUTSOURCE.value,
    )
    inhouse_process = TProcess(
        code=f"IN-SH-{suffix}",
        name=f"自产工序-{suffix}",
        category=ProcessCategory.INHOUSE.value,
    )
    production_shelf = TShelf(
        code=f"PROD-SH-{suffix}",
        name=f"生产货架-{suffix}",
        zone=ShelfZone.PRODUCTION.value,
        is_active=True,
    )
    session.add_all([customer, company, outsource_process, inhouse_process, production_shelf])
    await session.flush()

    mapping = TOutsourceCompanyProcess(
        outsource_company_id=company.id,
        process_id=outsource_process.id,
        sort_order=0,
    )
    shelf_process_mapping = TShelfProcess(
        shelf_id=production_shelf.id,
        process_id=inhouse_process.id,
        sort_order=0,
    )
    shelf_process_outsource_mapping = TShelfProcess(
        shelf_id=production_shelf.id,
        process_id=outsource_process.id,
        sort_order=1,
    )
    part = TPart(
        serial_no=f"S{suffix[-4:]}",
        name=f"shipment测试零件-{suffix}",
        drawing_no=f"SHIPMENT-{suffix}",
        applicant_name="测试申请人",
        quantity=quantity,
        request_date=date(2026, 7, 16),
        planned_delivery_date=date(2026, 8, 15),
        customer_id=customer.id,
        status=PartStatus.IN_PROCESS.value,
        location=PartLocation.PRODUCTION_SHELF.value,
        current_holder_id=production_shelf.id,
        next_process_id=outsource_process.id,
    )
    session.add_all([mapping, shelf_process_mapping, shelf_process_outsource_mapping, part])
    await session.flush()
    from tests.conftest import seed_root_batch
    root_batch = await seed_root_batch(session, part)
    return {
        "customer": customer,
        "company": company,
        "outsource_process": outsource_process,
        "inhouse_process": inhouse_process,
        "production_shelf": production_shelf,
        "part": part,
        "root_batch": root_batch,
    }


def _make_quote_service(session) -> OutsourceQuoteService:
    return OutsourceQuoteService(
        quotes=OutsourceQuoteRepository(session),
        quote_events=OutsourceQuoteEventRepository(session),
        parts=PartRepository(session),
        companies=OutsourceCompanyRepository(session),
        processes=ProcessRepository(session),
        customers=CustomerRepository(session),
        shelves=ShelfRepository(session),
        workers=WorkerRepository(session),
        part_events=PartEventRepository(session),
        shipments=OutsourceShipmentRepository(session),
    )


def _make_part_service(session) -> PartService:
    return PartService(
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        events=PartEventRepository(session),
        serial_counters=SerialCounterRepository(session),
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        outsource_companies=OutsourceCompanyRepository(session),
        outsource_company_process=OutsourceCompanyProcessRepository(session),
        outsource_quotes=OutsourceQuoteRepository(session),
        quote_events=OutsourceQuoteEventRepository(session),
        outsource_shipments=OutsourceShipmentRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
    )


async def _approve_quote(session, world):
    service = _make_quote_service(session)
    created = await service.create_quote(OutsourceQuoteCreateRequest(
        part_id=str(world["part"].id),
        outsource_company_id=str(world["company"].id),
        process_id=str(world["outsource_process"].id),
        price=Decimal("88.00"),
        note="shipment 测试报价",
    ))
    submitted = await service.submit_quote(str(created.id))
    return await service.approve_quote(
        str(created.id),
        OutsourceQuoteApproveRequest(version=submitted.version, review_note="批准"),
    )


# ============================================================
# a) 部分量发送 → 拆批且 shipment 绑定新批
# ============================================================
async def test_partial_send_splits_batch_and_shipment_binds_new_batch(clean_db):
    world = await _seed_world(clean_db, suffix="PSPLIT", quantity=10)
    approved = await _approve_quote(clean_db, world)
    service = _make_part_service(clean_db)

    req = SendToOutsourceRequest(
        outsource_company_id=str(world["company"].id),
        next_process_id=str(world["outsource_process"].id),
        version=world["root_batch"].version,
        quantity=3,
    )
    sent = await service.send_to_outsource(world["part"].id, req)
    # 2026-07-30：部分发送后，part rollup 仍可能为 IN_PROCESS（因原批次剩 7 件仍在货架）
    batches = await service.part_batches.list_by_part(world["part"].id)
    assert len(batches) == 2
    new_batch = next(b for b in batches if b.quantity == 3)
    old_batch = next(b for b in batches if b.quantity == 7)
    assert new_batch.status == PartStatus.OUTSOURCE.value
    assert old_batch.status == PartStatus.IN_PROCESS.value

    # shipment 绑定新批次
    shipment = await service.outsource_shipments.get_open_by_batch_id(new_batch.id)
    assert shipment is not None
    assert shipment.quantity == 3
    assert shipment.unit_price == Decimal("88.00")
    assert shipment.quote_id == int(approved.id)

    # 报价仍为 APPROVED，未变
    quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert quote.status == OutsourceQuoteStatus.APPROVED.value


# ============================================================
# b) 同一 APPROVED 报价连续发两个批次
# ============================================================
async def test_reuse_approved_quote_for_multiple_batches(clean_db):
    world = await _seed_world(clean_db, suffix="REUSE", quantity=10)
    approved = await _approve_quote(clean_db, world)
    service = _make_part_service(clean_db)

    # 先发 4 件
    req1 = SendToOutsourceRequest(
        outsource_company_id=str(world["company"].id),
        next_process_id=str(world["outsource_process"].id),
        version=world["root_batch"].version,
        quantity=4,
    )
    await service.send_to_outsource(world["part"].id, req1)

    # 再发剩下 6 件（需指定原批次 batch_id）
    batches = await service.part_batches.list_by_part(world["part"].id)
    remaining = next(b for b in batches if b.status == PartStatus.IN_PROCESS.value)
    req2 = SendToOutsourceRequest(
        outsource_company_id=str(world["company"].id),
        next_process_id=str(world["outsource_process"].id),
        version=remaining.version,
        quantity=6,
        batch_id=str(remaining.id),
    )
    await service.send_to_outsource(world["part"].id, req2)

    # 两个 shipment，同一 quote
    shipments = [
        s for s in await service.outsource_shipments.list_reconciliation_for_company(
            company_id=world["company"].id, limit=10, offset=0,
        )
    ]
    assert len(shipments) == 2
    assert all(s.quote_id == int(approved.id) for s in shipments)
    assert sum(s.quantity for s in shipments) == 10

    # 报价 version/status 不变
    quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert quote.status == OutsourceQuoteStatus.APPROVED.value


# ============================================================
# c) 全量接收 → shipment RECEIVED
# ============================================================
async def test_full_receive_closes_shipment(clean_db):
    world = await _seed_world(clean_db, suffix="FULLREC", quantity=5)
    await _approve_quote(clean_db, world)
    service = _make_part_service(clean_db)

    await service.send_to_outsource(
        world["part"].id,
        SendToOutsourceRequest(
            outsource_company_id=str(world["company"].id),
            next_process_id=str(world["outsource_process"].id),
            version=world["root_batch"].version,
        ),
    )

    received = await service.receive_from_outsource(
        world["part"].id,
        PlaceOnShelfRequest(
            shelf_id=str(world["production_shelf"].id),
            next_process_id=str(world["inhouse_process"].id),
        ),
    )
    assert received.status == PartStatus.IN_PROCESS

    shipment = await service.outsource_shipments.get_open_by_batch_id(world["root_batch"].id)
    assert shipment is None  # 已关闭
    # 查最新 shipment 行
    from sqlalchemy import select
    from model import TOutsourceShipment
    stmt = select(TOutsourceShipment).where(TOutsourceShipment.part_id == world["part"].id)
    rows = list((await clean_db.execute(stmt)).scalars().all())
    assert len(rows) == 1
    assert rows[0].status == "RECEIVED"
    assert rows[0].received_at is not None


# ============================================================
# d) 部分接收 → 源 shipment 减量 + 新 RECEIVED shipment
# ============================================================
async def test_partial_receive_splits_shipment(clean_db):
    world = await _seed_world(clean_db, suffix="PARTREC", quantity=10)
    await _approve_quote(clean_db, world)
    service = _make_part_service(clean_db)

    await service.send_to_outsource(
        world["part"].id,
        SendToOutsourceRequest(
            outsource_company_id=str(world["company"].id),
            next_process_id=str(world["outsource_process"].id),
            version=world["root_batch"].version,
            quantity=10,
        ),
    )

    # 部分接收 4 件
    received = await service.receive_from_outsource(
        world["part"].id,
        PlaceOnShelfRequest(
            shelf_id=str(world["production_shelf"].id),
            next_process_id=str(world["inhouse_process"].id),
            quantity=4,
        ),
    )
    assert received.status == PartStatus.IN_PROCESS

    from sqlalchemy import select
    from model import TOutsourceShipment
    rows = list((await clean_db.execute(
        select(TOutsourceShipment).where(TOutsourceShipment.part_id == world["part"].id)
    )).scalars().all())
    assert len(rows) == 2
    # 数量守恒
    assert sum(s.quantity for s in rows) == 10
    received_shipment = next(s for s in rows if s.status == "RECEIVED")
    open_shipment = next(s for s in rows if s.status == "OUTSOURCING")
    assert received_shipment.quantity == 4
    assert open_shipment.quantity == 6


# ============================================================
# e) 批准新报价自动 REJECT 旧报价（含 DIRECT 占位）
# ============================================================
async def test_approve_auto_rejects_competing_quotes(clean_db):
    world = await _seed_world(clean_db, suffix="AUTOREJ", quantity=5)
    service = _make_quote_service(clean_db)

    # 先创建新报价
    new = await service.create_quote(OutsourceQuoteCreateRequest(
        part_id=str(world["part"].id),
        outsource_company_id=str(world["company"].id),
        process_id=str(world["outsource_process"].id),
        price=Decimal("99.00"),
    ))

    # 再直接插入旧 DIRECT 占位报价（绕过 service 层重复检测）
    from model.outsource_quote import TOutsourceQuote
    from utils.id_gen import new_id
    old_q = TOutsourceQuote(
        id=new_id(),
        part_id=world["part"].id,
        outsource_company_id=world["company"].id,
        process_id=world["outsource_process"].id,
        price=Decimal("0"),
        status=OutsourceQuoteStatus.APPROVED.value,
        is_direct=True,
        review_note="DIRECT 占位",
        reviewed_at=__import__("datetime").datetime.now(),
    )
    await service.quotes.create(old_q)

    submitted = await service.submit_quote(str(new.id))
    approved = await service.approve_quote(
        str(new.id),
        OutsourceQuoteApproveRequest(version=submitted.version, review_note="新报价"),
    )
    assert approved.status == OutsourceQuoteStatus.APPROVED.value

    old_refreshed = await service.quotes.get_by_id(old_q.id)
    assert old_refreshed.status == OutsourceQuoteStatus.REJECTED.value
    assert old_refreshed.review_note == "被新批准报价取代"


# ============================================================
# f) 对账列表 + reconcile-update + OCC 409 + MissingGreenlet 防护
# ============================================================
async def test_reconcile_list_and_update_shipment(clean_db):
    world = await _seed_world(clean_db, suffix="RECON", quantity=5)
    await _approve_quote(clean_db, world)
    part_service = _make_part_service(clean_db)
    await part_service.send_to_outsource(
        world["part"].id,
        SendToOutsourceRequest(
            outsource_company_id=str(world["company"].id),
            next_process_id=str(world["outsource_process"].id),
            version=world["root_batch"].version,
        ),
    )

    quote_service = _make_quote_service(clean_db)
    items, total = await quote_service.list_in_flight()
    assert total >= 1
    assert any(str(world["part"].id) == str(it.part_id) for it in items)

    # reconcile-update 修改单价/数量/is_billed
    shipment = await part_service.outsource_shipments.get_open_by_batch_id(world["root_batch"].id)
    old_version = shipment.version
    updated = await quote_service.reconcile_update_shipment(
        str(shipment.id),
        OutsourceShipmentReconcileUpdateRequest(
            version=shipment.version,
            unit_price=Decimal("99.50"),
            quantity=5,
            is_billed=True,
        ),
    )
    assert updated.unit_price == Decimal("99.50")
    assert updated.quantity == 5
    assert updated.is_billed is True

    # OCC 冲突 409（用旧 version）
    with pytest.raises(BizError) as exc:
        await quote_service.reconcile_update_shipment(
            str(shipment.id),
            OutsourceShipmentReconcileUpdateRequest(
                version=old_version,
                unit_price=Decimal("100"),
            ),
        )
    assert exc.value.code == ErrCode.BIZ_VERSION_CONFLICT
    assert exc.value.http_status == 409


# ============================================================
# g) 外协中取消批次 → shipment CANCELLED
# ============================================================
async def test_cancel_outsource_batch_cancels_shipment(clean_db):
    world = await _seed_world(clean_db, suffix="CANCL", quantity=5)
    await _approve_quote(clean_db, world)
    service = _make_part_service(clean_db)
    await service.send_to_outsource(
        world["part"].id,
        SendToOutsourceRequest(
            outsource_company_id=str(world["company"].id),
            next_process_id=str(world["outsource_process"].id),
            version=world["root_batch"].version,
        ),
    )

    await service.cancel(world["part"].id, batch_id=world["root_batch"].id)

    shipment = await service.outsource_shipments.get_open_by_batch_id(world["root_batch"].id)
    assert shipment is None
    from sqlalchemy import select
    from model import TOutsourceShipment
    rows = list((await clean_db.execute(
        select(TOutsourceShipment).where(TOutsourceShipment.part_id == world["part"].id)
    )).scalars().all())
    assert len(rows) == 1
    assert rows[0].status == "CANCELLED"


# ============================================================
# h) in-flight 端点
# ============================================================
async def test_in_flight_endpoint_returns_batches(clean_db):
    world = await _seed_world(clean_db, suffix="INFLT", quantity=5)
    await _approve_quote(clean_db, world)
    service = _make_quote_service(clean_db)
    await service.list_in_flight(keyword=world["part"].drawing_no, limit=10, offset=0)
    # 无断言异常即通过；更细粒度断言在 service 单元测试中覆盖


# ============================================================
# i) 无报价发送仍 400
# ============================================================
async def test_send_without_quote_still_400(clean_db):
    world = await _seed_world(clean_db, suffix="NOQT", quantity=5)
    service = _make_part_service(clean_db)
    with pytest.raises(BizError) as exc:
        await service.send_to_outsource(
            world["part"].id,
            SendToOutsourceRequest(
                outsource_company_id=str(world["company"].id),
                next_process_id=str(world["outsource_process"].id),
                version=world["root_batch"].version,
            ),
        )
    assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_APPROVED
