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
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
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


async def _seed_world(session, *, suffix: str, part_status: str = "PENDING"):
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
        location=(
            PartLocation.OFFICE.value
            if part_status == PartStatus.PENDING.value
            else None
        ),
    )
    session.add_all([mapping, shelf_process_mapping, part])
    await session.flush()
    return {
        "customer": customer,
        "company": company,
        "outsource_process": outsource_process,
        "inhouse_process": inhouse_process,
        "production_shelf": production_shelf,
        "inspection_shelf": inspection_shelf,
        "part": part,
    }


def _make_quote_service(session) -> OutsourceQuoteService:
    return OutsourceQuoteService(
        quotes=OutsourceQuoteRepository(session),
        quote_events=OutsourceQuoteEventRepository(session),
        parts=PartRepository(session),
        companies=OutsourceCompanyRepository(session),
        processes=ProcessRepository(session),
        customers=CustomerRepository(session),
        part_events=PartEventRepository(session),
    )


def _make_part_service(session, *, event_broadcaster=None) -> PartService:
    return PartService(
        parts=PartRepository(session),
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


def _send_request(world) -> SendToOutsourceRequest:
    return SendToOutsourceRequest(
        outsource_company_id=str(world["company"].id),
        next_process_id=str(world["outsource_process"].id),
        version=world["part"].version,
    )


async def test_send_to_outsource_marks_quote_used_then_receive_to_production(clean_db):
    world = await _seed_world(clean_db, suffix="FLOW")
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

    used_quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert used_quote is not None
    assert used_quote.status == OutsourceQuoteStatus.USED.value
    quote_events = await service.quote_events.list_by_quote(used_quote.id)
    assert quote_events[-1].event_type == OutsourceQuoteEventType.USED.value

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

    part_events = await service.events.list_by_part(world["part"].id)
    assert [event.event_type for event in part_events] == [
        PartEventType.QUOTE_CREATED.value,
        PartEventType.QUOTE_APPROVED.value,
        PartEventType.SENT_TO_OUTSOURCE.value,
        PartEventType.RECEIVED_FROM_OUTSOURCE.value,
    ]


async def test_send_to_outsource_defensive_guards_leave_part_unchanged(clean_db):
    no_quote_world = await _seed_world(clean_db, suffix="NOQUOTE")
    service = _make_part_service(clean_db)

    with pytest.raises(BizError) as no_quote_exc:
        await service.send_to_outsource(
            no_quote_world["part"].id, _send_request(no_quote_world),
        )
    assert no_quote_exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_APPROVED
    assert no_quote_world["part"].status == PartStatus.PENDING.value
    assert no_quote_world["part"].location == PartLocation.OFFICE.value

    wrong_state_world = await _seed_world(
        clean_db,
        suffix="BADSTATE",
        part_status=PartStatus.READY_TO_SHIP.value,
    )
    await _approve_quote(clean_db, wrong_state_world)
    with pytest.raises(BizError) as state_exc:
        await service.send_to_outsource(
            wrong_state_world["part"].id, _send_request(wrong_state_world),
        )
    assert state_exc.value.code == ErrCode.BIZ_PART_NOT_OUTSOURCEABLE
    assert wrong_state_world["part"].status == PartStatus.READY_TO_SHIP.value


async def test_receive_to_inspection_auto_pass_refreshes_expired_state(clean_db):
    world = await _seed_world(clean_db, suffix="AUTOPASS")
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

    used_quote = await service.outsource_quotes.get_by_id(int(approved.id))
    assert used_quote is not None
    assert used_quote.status == OutsourceQuoteStatus.USED.value

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
