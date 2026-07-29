"""外协报价状态机真实 PostgreSQL 生命周期集成测试。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from core.error_code import ErrCode
from core.exception import BizError
from model import TCustomer, TOutsourceCompany, TOutsourceQuote, TPart, TProcess
from model.enums import (
    OutsourceQuoteEventType,
    OutsourceQuoteStatus,
    PartStatus,
    ProcessCategory,
)
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.worker import WorkerRepository
from schema.outsource_quote import (
    OutsourceQuoteApproveRequest,
    OutsourceQuoteCreateRequest,
)
from service._session_refresh import refresh_for_state_machine
from service.outsource_quote import OutsourceQuoteService
from utils.id_gen import new_id

pytestmark = pytest.mark.asyncio


async def _seed_quote_dependencies(session, *, suffix: str):
    customer = TCustomer(name=f"报价生命周期测试客户-{suffix}")
    process = TProcess(
        code=f"OUT-QUOTE-{suffix}",
        name=f"报价生命周期外协工序-{suffix}",
        category=ProcessCategory.OUTSOURCE.value,
    )
    company = TOutsourceCompany(
        name=f"报价生命周期外协公司-{suffix}", is_active=True,
    )

    session.add(customer)
    await session.flush()
    part = TPart(
        serial_no=f"Q{suffix[-4:]}",
        name=f"报价生命周期测试零件-{suffix}",
        drawing_no=f"QUOTE-LIFECYCLE-{suffix}",
        applicant_name="测试申请人",
        quantity=1,
        request_date=date(2026, 7, 16),
        planned_delivery_date=date(2026, 7, 31),
        customer_id=customer.id,
        status=PartStatus.PENDING.value,
        location="OFFICE",
    )
    session.add_all([part, process, company])
    await session.flush()
    return part, process, company


def _make_service(session) -> OutsourceQuoteService:
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


async def _create_quote(
    service: OutsourceQuoteService,
    *,
    part: TPart,
    process: TProcess,
    company: TOutsourceCompany,
    price: str = "88.50",
):
    return await service.create_quote(OutsourceQuoteCreateRequest(
        part_id=str(part.id),
        outsource_company_id=str(company.id),
        process_id=str(process.id),
        price=Decimal(price),
        note="真实 DB 生命周期",
    ))


async def test_quote_lifecycle_draft_to_approved_without_missing_greenlet(clean_db):
    """回归原始故障：UPDATE 后序列化 updated_at 不得隐式 lazy-load。"""
    part, process, company = await _seed_quote_dependencies(
        clean_db, suffix="LIFE",
    )
    service = _make_service(clean_db)
    quotes = service.quotes
    events = service.quote_events

    created = await _create_quote(
        service, part=part, process=process, company=company,
    )
    assert created.status == OutsourceQuoteStatus.DRAFT
    initial_version = created.version

    submitted = await service.submit_quote(str(created.id))
    assert submitted.status == OutsourceQuoteStatus.SUBMITTED
    assert submitted.submitted_at is not None
    assert submitted.version > initial_version

    approved = await service.approve_quote(
        str(created.id),
        OutsourceQuoteApproveRequest(
            version=submitted.version, review_note="同意外协",
        ),
    )
    assert approved.status == OutsourceQuoteStatus.APPROVED
    assert approved.reviewed_at is not None
    assert approved.review_note == "同意外协"
    assert approved.version > submitted.version

    quote = await quotes.get_by_id(int(created.id))
    assert quote is not None
    await refresh_for_state_machine(
        clean_db, quote, attrs=("status", "version"),
    )
    # 2026-07-30：报价回归纯审批对象，终态为 APPROVED/REJECTED
    assert quote.status == OutsourceQuoteStatus.APPROVED.value
    rows = await events.list_by_quote(quote.id)
    assert [row.event_type for row in rows] == [
        OutsourceQuoteEventType.CREATED.value,
        OutsourceQuoteEventType.SUBMITTED.value,
        OutsourceQuoteEventType.APPROVED.value,
    ]
    assert [(row.from_status, row.to_status) for row in rows] == [
        (None, OutsourceQuoteStatus.DRAFT.value),
        (OutsourceQuoteStatus.DRAFT.value, OutsourceQuoteStatus.SUBMITTED.value),
        (OutsourceQuoteStatus.SUBMITTED.value, OutsourceQuoteStatus.APPROVED.value),
    ]


async def test_duplicate_active_tuple_hits_service_guard_and_db_integrity(clean_db):
    part, process, company = await _seed_quote_dependencies(
        clean_db, suffix="DUPE",
    )
    service = _make_service(clean_db)
    created = await _create_quote(
        service, part=part, process=process, company=company,
    )

    with pytest.raises(BizError) as exc_info:
        await _create_quote(
            service, part=part, process=process, company=company,
        )
    assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_DUPLICATE

    # 2026-07-30：DB 部分唯一索引改为 (part_id, process_id) WHERE APPROVED AND is_direct=false。
    # DRAFT 重复不再撞 DB 索引，只由 service 层拦截。
    duplicate = TOutsourceQuote(
        id=new_id(),
        part_id=part.id,
        outsource_company_id=company.id,
        process_id=process.id,
        price=Decimal("99.00"),
        status=OutsourceQuoteStatus.DRAFT.value,
    )
    clean_db.add(duplicate)
    await clean_db.flush()  # 不应抛 IntegrityError

    persisted = await service.quotes.get_by_id(int(created.id))
    assert persisted is not None
    assert persisted.status == OutsourceQuoteStatus.DRAFT.value


async def test_quote_occ_conflict_is_detected_by_version_column(clean_db):
    from core.database import engine

    part, process, company = await _seed_quote_dependencies(
        clean_db, suffix="OCC",
    )
    service = _make_service(clean_db)
    created = await _create_quote(
        service, part=part, process=process, company=company,
    )
    submitted = await service.submit_quote(str(created.id))
    await clean_db.commit()

    SessionB = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionB() as session_b:
        stale_quote = await session_b.get(TOutsourceQuote, int(created.id))
        assert stale_quote is not None
        stale_version = stale_quote.version

        current_quote = await clean_db.get(TOutsourceQuote, int(created.id))
        assert current_quote is not None
        current_quote.note = "session A 抢先更新"
        await clean_db.flush()
        await clean_db.commit()

        stale_quote.note = "session B 的过期更新"
        with pytest.raises(StaleDataError):
            await session_b.flush()

    with pytest.raises(BizError) as exc_info:
        await service.approve_quote(
            str(created.id),
            OutsourceQuoteApproveRequest(version=stale_version),
        )
    assert exc_info.value.code == ErrCode.BIZ_VERSION_CONFLICT
    assert submitted.status == OutsourceQuoteStatus.SUBMITTED


async def test_soft_delete_only_allows_draft_or_rejected(clean_db):
    service = _make_service(clean_db)

    part, process, company = await _seed_quote_dependencies(
        clean_db, suffix="KEEP",
    )
    submitted = await service.submit_quote(str((await _create_quote(
        service, part=part, process=process, company=company,
    )).id))
    with pytest.raises(BizError) as exc_info:
        await service.soft_delete_quote(str(submitted.id))
    assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION

    draft_part, draft_process, draft_company = await _seed_quote_dependencies(
        clean_db, suffix="DROP",
    )
    draft = await _create_quote(
        service,
        part=draft_part,
        process=draft_process,
        company=draft_company,
    )
    await service.soft_delete_quote(str(draft.id))

    with pytest.raises(BizError) as missing_exc:
        await service.get_quote(str(draft.id))
    assert missing_exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_FOUND
    deleted = await service.quotes.get_by_id(
        int(draft.id), include_deleted=True,
    )
    assert deleted is not None
    assert deleted.deleted_at is not None
