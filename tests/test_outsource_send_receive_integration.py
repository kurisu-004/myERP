"""零件外协发送/回收真实 PostgreSQL 集成测试。"""
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
    OutsourceQuoteEventType,
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
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.worker import WorkerRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.worker import WorkerRepository
from schema.outsource_quote import (
    OutsourceQuoteApproveRequest,
    OutsourceQuoteCreateRequest,
)
from schema.part import (
    PlaceOnShelfRequest,
    ReceiveToInspectionRequest,
    SendToOutsourceRequest,
)
from service.outsource_quote import OutsourceQuoteService
from service.part import PartService

pytestmark = pytest.mark.asyncio


async def _seed_world(
    session, *, suffix: str,
    part_status: str = PartStatus.IN_PROCESS.value,
    part_location: str = PartLocation.PRODUCTION_SHELF.value,
    part_holder_id: int | None = None,
):
    """PR-H 2026-07-28：默认 part 是 IN_PROCESS + PRODUCTION_SHELF + 在某货架上。"""
    customer = TCustomer(name=f"外协收发测试客户-{suffix}")
    customer = TCustomer(name=f"外协收发测试客户-{suffix}")
    company = TOutsourceCompany(
        name=f"外协收发测试公司-{suffix}", is_active=True,
    )
    outsource_process = TProcess(
        code=f"OUT-SR-{suffix}",
        name=f"外协工序-{suffix}",
        category=ProcessCategory.OUTSOURCE.value,
    )
    inhouse_process = TProcess(
        code=f"IN-SR-{suffix}",
        name=f"自产工序-{suffix}",
        category=ProcessCategory.INHOUSE.value,
    )
    production_shelf = TShelf(
        code=f"PROD-{suffix}",
        name=f"生产货架-{suffix}",
        zone=ShelfZone.PRODUCTION.value,
        is_active=True,
    )
    inspection_shelf = TShelf(
        code=f"INSP-{suffix}",
        name=f"品检货架-{suffix}",
        zone=ShelfZone.INSPECTION.value,
        is_active=True,
    )
    session.add_all([
        customer,
        company,
        outsource_process,
        inhouse_process,
        production_shelf,
        inspection_shelf,
    ])
    await session.flush()

    mapping = TOutsourceCompanyProcess(
        outsource_company_id=company.id,
        process_id=outsource_process.id,
        sort_order=0,
    )
    # 货架↔工序映射：receive_from_outsource 落生产货架时的 _assert_shelf_maps_process 需要
    shelf_process_mapping = TShelfProcess(
        shelf_id=production_shelf.id,
        process_id=inhouse_process.id,
        sort_order=0,
    )
    # PR-H 2026-07-28：外协发送统一要求货架同时绑定了 OUTSOURCE 工序
    shelf_process_outsource_mapping = TShelfProcess(
        shelf_id=production_shelf.id,
        process_id=outsource_process.id,
        sort_order=1,
    )
    part = TPart(
        serial_no=f"O{suffix[-4:]}",
        name=f"外协收发测试零件-{suffix}",
        drawing_no=f"OUTSOURCE-SEND-RECEIVE-{suffix}",
        applicant_name="外协测试申请人",
        quantity=1,
        request_date=date(2026, 7, 16),
        planned_delivery_date=date(2026, 8, 15),
        customer_id=customer.id,
        status=part_status,
        location=part_location,
        current_holder_id=part_holder_id,
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
        "inspection_shelf": inspection_shelf,
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
    )


def _make_part_service(session, *, event_broadcaster=None) -> PartService:
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
        shelf_process_repo=ShelfProcessRepository(session),
        event_broadcaster=event_broadcaster,
    )


async def _approve_quote(session, world):
    service = _make_quote_service(session)
    created = await service.create_quote(OutsourceQuoteCreateRequest(
        part_id=str(world["part"].id),
        outsource_company_id=str(world["company"].id),
        process_id=str(world["outsource_process"].id),
        price=Decimal("120.00"),
        note="外协发送集成测试报价",
    ))
    submitted = await service.submit_quote(str(created.id))
    return await service.approve_quote(
        str(created.id),
        OutsourceQuoteApproveRequest(
            version=submitted.version, review_note="批准发送",
        ),
    )


async def _place_on_shelf(session, world) -> None:
    """2026-07-29 批次化：测试夹具把 part 摆上货架时，part 与根批次同步。

    批次是状态机载体：service 只读批次；part 字段由 rollup 派生。
    """
    part = world["part"]
    batch = world["root_batch"]
    for m in (part, batch):
        m.location = PartLocation.PRODUCTION_SHELF.value
        m.current_holder_id = world["production_shelf"].id
        m.next_process_id = world["outsource_process"].id
    await session.flush()


def _send_request(world) -> SendToOutsourceRequest:
    return SendToOutsourceRequest(
        outsource_company_id=str(world["company"].id),
        next_process_id=str(world["outsource_process"].id),
        version=world["part"].version,
    )


async def test_send_to_outsource_marks_quote_used_then_receive_to_production(clean_db):
    world = await _seed_world(clean_db, suffix="FLOW")
    # PR-H 2026-07-28：把 part 放到绑了 OUTSOURCE 工序的货架上（默认已是 IN_PROCESS 但
    # holder 未设；下面设到 production_shelf 上）
    await _place_on_shelf(clean_db, world)
    approved = await _approve_quote(clean_db, world)
    service = _make_part_service(clean_db)
    original_version = world["part"].version

    sent = await service.send_to_outsource(
        world["part"].id, _send_request(world),
    )
    assert sent.status == PartStatus.OUTSOURCE
    assert sent.location == PartLocation.OUTSOURCE_COMPANY.value
    assert sent.current_holder_id == world["company"].id
    assert sent.next_process_id == world["outsource_process"].id
    assert sent.version > original_version

    # PR-H 2026-07-29：发送后报价为 OUTSOURCING（不再是 USED）
    sent_quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert sent_quote is not None
    assert sent_quote.status == OutsourceQuoteStatus.OUTSOURCING.value
    assert sent_quote.sent_at is not None
    assert sent_quote.quantity == world["part"].quantity
    quote_events = await service.quote_events.list_by_quote(sent_quote.id)
    assert quote_events[-1].event_type == OutsourceQuoteEventType.MARKED_OUTSOURCING.value

    received = await service.receive_from_outsource(
        world["part"].id,
        PlaceOnShelfRequest(
            shelf_id=world["production_shelf"].id,
            next_process_id=world["inhouse_process"].id,
        ),
    )
    assert received.status == PartStatus.IN_PROCESS
    assert received.location == PartLocation.PRODUCTION_SHELF.value
    assert received.current_holder_id == world["production_shelf"].id
    assert received.next_process_id == world["inhouse_process"].id

    # PR-H 2026-07-29：接收后报价为 RECEIVED + received_at 已写
    received_quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert received_quote is not None
    assert received_quote.status == OutsourceQuoteStatus.RECEIVED.value
    assert received_quote.received_at is not None

    part_events = await service.events.list_by_part(world["part"].id)
    assert [event.event_type for event in part_events] == [
        PartEventType.QUOTE_CREATED.value,
        PartEventType.QUOTE_APPROVED.value,
        PartEventType.SENT_TO_OUTSOURCE.value,
        PartEventType.RECEIVED_FROM_OUTSOURCE.value,
    ]


async def test_direct_send_persists_zero_price_outsourcing_quote(clean_db):
    """DIRECT 发送允许持久化待对账补价的零价占位记录。"""
    world = await _seed_world(clean_db, suffix="DIRECT")
    world["outsource_process"].requires_approval = False
    await _place_on_shelf(clean_db, world)
    service = _make_part_service(clean_db)

    sent = await service.send_to_outsource(
        world["part"].id, _send_request(world),
    )

    assert sent.status == PartStatus.OUTSOURCE
    assert sent.location == PartLocation.OUTSOURCE_COMPANY.value
    quote = await service.outsource_quotes.get_one_active_for_tuple(
        part_id=world["part"].id,
        outsource_company_id=world["company"].id,
        process_id=world["outsource_process"].id,
    )
    assert quote is not None
    assert quote.price == Decimal("0")
    assert quote.status == OutsourceQuoteStatus.OUTSOURCING.value
    assert quote.quantity == world["root_batch"].quantity
    assert quote.review_note == "系统自动创建（DIRECT 直接发送）"


async def test_send_to_outsource_defensive_guards_leave_part_unchanged(clean_db):
    no_quote_world = await _seed_world(clean_db, suffix="NOQUOTE")
    # PR-H 2026-07-28：放到 OUTSOURCE-bound 货架上
    await _place_on_shelf(clean_db, no_quote_world)
    service = _make_part_service(clean_db)

    with pytest.raises(BizError) as no_quote_exc:
        await service.send_to_outsource(
            no_quote_world["part"].id, _send_request(no_quote_world),
        )
    assert no_quote_exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_APPROVED
    # PR-H 2026-07-28：默认 part 已在 OUTSOURCE-bound 货架上（IN_PROCESS+PRODUCTION_SHELF）
    assert no_quote_world["part"].status == PartStatus.IN_PROCESS.value
    assert no_quote_world["part"].location == PartLocation.PRODUCTION_SHELF.value

    wrong_state_world = await _seed_world(
        clean_db,
        suffix="BADSTATE",
        part_status=PartStatus.READY_TO_SHIP.value,
        part_location=PartLocation.OFFICE.value,
        part_holder_id=None,
    )
    await _approve_quote(clean_db, wrong_state_world)
    with pytest.raises(BizError) as state_exc:
        await service.send_to_outsource(
            wrong_state_world["part"].id, _send_request(wrong_state_world),
        )
    # PR-H 2026-07-28：统一走 OUTSOURCE-bound 货架闸门；READY_TO_SHIP + OFFICE 抛
    # BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF 422
    assert state_exc.value.code == ErrCode.BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF
    assert wrong_state_world["part"].status == PartStatus.READY_TO_SHIP.value


async def test_receive_to_inspection_auto_pass_refreshes_expired_state(clean_db):
    world = await _seed_world(clean_db, suffix="AUTOPASS")
    # PR-H 2026-07-28：放到 OUTSOURCE-bound 货架上
    await _place_on_shelf(clean_db, world)
    approved = await _approve_quote(clean_db, world)

    async def expire_state_after_first_transition(event_type: str, _payload: dict):
        if event_type == "RECEIVED_FROM_OUTSOURCE_INSPECTED":
            clean_db.expire(
                world["part"],
                attribute_names=["status", "location", "next_process_id"],
            )

    service = _make_part_service(
        clean_db, event_broadcaster=expire_state_after_first_transition,
    )
    await service.send_to_outsource(world["part"].id, _send_request(world))

    received = await service.receive_from_outsource_to_inspection(
        world["part"].id,
        ReceiveToInspectionRequest(
            shelf_id=str(world["inspection_shelf"].id),
            auto_pass_inspection=True,
        ),
    )
    assert received.status == PartStatus.READY_TO_SHIP
    assert received.location is None

    # PR-H 2026-07-29：接收后报价为 RECEIVED（不再是 USED）
    received_quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert received_quote is not None
    assert received_quote.status == OutsourceQuoteStatus.RECEIVED.value
    assert received_quote.received_at is not None

    part_events = await service.events.list_by_part(world["part"].id)
    assert [event.event_type for event in part_events] == [
        PartEventType.QUOTE_CREATED.value,
        PartEventType.QUOTE_APPROVED.value,
        PartEventType.SENT_TO_OUTSOURCE.value,
        PartEventType.INSPECTED.value,
        PartEventType.STATUS_CHANGED.value,
    ]
    assert (
        part_events[-1].from_status,
        part_events[-1].to_status,
    ) == (
        PartStatus.INSPECTION.value,
        PartStatus.READY_TO_SHIP.value,
    )
