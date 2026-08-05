"""外协报价 (OutsourceQuote) service 单元测试（2026-07-16 新增）。

mirror tests/unit/test_outsource_company_service.py 风格。
覆盖：
- create_quote 正常 + 异常（缺 part / company / process / 类别错 / 重复 tuple）
- update_quote DRAFT-only + 乐观锁
- submit / approve / reject / mark_used 4 个状态机转换
- soft_delete DRAFT / REJECTED only 校验
- 单条 get_quote
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.enums import OutsourceQuoteEventType, OutsourceQuoteStatus, ProcessCategory
from model.outsource_company import TOutsourceCompany
from model.outsource_quote import TOutsourceQuote
from model.outsource_quote_event import TOutsourceQuoteEvent
from model.process import TProcess
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_quote import OutsourceQuoteRepository
from repository.outsource_quote_event import OutsourceQuoteEventRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from schema.outsource_quote import (
    OutsourceQuoteApproveRequest,
    OutsourceQuoteCreateRequest,
    OutsourceQuoteRejectRequest,
    OutsourceQuoteUpdateRequest,
)
from service.outsource_quote import OutsourceQuoteService


# =============================================================================
# Fixtures & factories
# =============================================================================
def _now() -> datetime:
    return datetime(2026, 7, 16, 12, 0, 0)


def _make_part(
    id: int = 100,
    serial_no: str = "F1000",
    drawing_no: str = "DWG-001",
    name: str = "测试零件",
    quantity: int = 5,
    customer_id: int | None = 200,
):
    from datetime import date
    from decimal import Decimal
    p = MagicMock()
    p.id = id
    p.serial_no = serial_no
    p.drawing_no = drawing_no
    p.name = name
    p.quantity = quantity
    p.customer_id = customer_id
    p.deleted_at = None
    # 2026-07-29 PR-fix-0.2.0 hotfix：PartListItem 走 Pydantic 校验，
    # 这些字段如果留 MagicMock 会触发 ValidationError。给上安全默认值。
    p.applicant_name = "(未知)"
    p.unit_price = Decimal("0")
    p.total_price = Decimal("0")
    p.request_date = date(2026, 7, 1)
    p.planned_delivery_date = date(2026, 8, 1)
    p.actual_delivery_date = None
    p.is_urgent = False
    p.order_no = None
    p.system_delivery_date = None
    p.note = None
    p.delivery_note_id = None
    return p


def _make_batch(
    id: int = 999,
    part_id: int = 100,
    batch_no: int = 1,
    quantity: int = 10,
    status: str = "PENDING",
    location: str | None = "OFFICE",
    current_holder_id: int | None = None,
    next_process_id: int | None = None,
    version: int = 1,
):
    """Mock 一个 TPartBatch（2026-07-29 批次化后测试用）。"""
    b = MagicMock()
    b.id = id
    b.part_id = part_id
    b.batch_no = batch_no
    b.quantity = quantity
    b.status = status
    b.location = location
    b.current_holder_id = current_holder_id
    b.next_process_id = next_process_id
    b.version = version
    b.deleted_at = None
    return b


def _make_company(id: int = 10, name: str = "外协A", is_active: bool = True) -> TOutsourceCompany:
    c = TOutsourceCompany(id=id, name=name, is_active=is_active)
    c.created_at = _now()
    c.updated_at = _now()
    c.deleted_at = None
    return c


def _make_process(id: int = 20, code: str = "PR-OUT-1", category: str = "OUTSOURCE") -> TProcess:
    p = TProcess(id=id, code=code, name=f"{code}名称", category=category, sort_order=0)
    p.created_at = _now()
    p.updated_at = _now()
    p.description = None
    p.deleted_at = None
    return p


def _make_quote(
    id: int = 999,
    part_id: int = 100,
    outsource_company_id: int = 10,
    process_id: int = 20,
    price: Decimal = Decimal("12.50"),
    status: str = OutsourceQuoteStatus.DRAFT.value,
    version: int = 1,
) -> TOutsourceQuote:
    q = TOutsourceQuote(
        id=id,
        part_id=part_id, outsource_company_id=outsource_company_id, process_id=process_id,
        price=price,
        status=status,
    )
    q.version = version
    q.created_at = _now()
    q.updated_at = _now()
    q.submitted_at = None
    q.reviewed_at = None
    q.review_note = None
    q.note = None
    q.deleted_at = None
    return q


# ----- Mock repos -----
@pytest.fixture
def mock_quotes() -> OutsourceQuoteRepository:
    repo = OutsourceQuoteRepository.__new__(OutsourceQuoteRepository)
    repo.session = MagicMock()
    repo.create = AsyncMock(side_effect=lambda q: q)
    repo.update = AsyncMock(side_effect=lambda q: q)
    repo.soft_delete = AsyncMock(side_effect=lambda q: q)
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_one_approved = AsyncMock(return_value=None)
    repo.get_approved_for_part_process = AsyncMock(return_value=None)
    repo.get_one_active_for_tuple = AsyncMock(return_value=None)
    repo.list_active_by_part_process = AsyncMock(return_value=[])
    repo.list_by_part_with_status = AsyncMock(return_value=[])
    repo.list_approved_for_part_ids = AsyncMock(return_value=[])
    repo.list_with_filters = AsyncMock(return_value=[])
    repo.count_with_filters = AsyncMock(return_value=0)
    return repo


@pytest.fixture
def mock_quote_events() -> OutsourceQuoteEventRepository:
    repo = OutsourceQuoteEventRepository.__new__(OutsourceQuoteEventRepository)
    repo.session = MagicMock()
    repo.add = MagicMock(side_effect=lambda ev: ev)
    repo.create = AsyncMock(side_effect=lambda ev: ev)
    repo.list_by_quote = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_parts() -> PartRepository:
    repo = PartRepository.__new__(PartRepository)
    repo.session = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.list_by_ids = AsyncMock(return_value=[])
    repo.list_with_filters = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_companies() -> OutsourceCompanyRepository:
    repo = OutsourceCompanyRepository.__new__(OutsourceCompanyRepository)
    repo.session = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_processes() -> ProcessRepository:
    repo = ProcessRepository.__new__(ProcessRepository)
    repo.session = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_customers() -> CustomerRepository:
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.session = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.list_by_ids = AsyncMock(return_value=[])
    repo.list_children = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_part_events() -> PartEventRepository:
    repo = PartEventRepository.__new__(PartEventRepository)
    repo.session = MagicMock()
    repo.create = AsyncMock()
    return repo


@pytest.fixture
def mock_shelves():
    """PR-H 2026-07-28：PartListItem 拼装需要 shelf_code。"""
    from repository.shelf import ShelfRepository
    repo = ShelfRepository.__new__(ShelfRepository)
    repo.session = MagicMock()
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_workers():
    """PR-H 2026-07-28：PartListItem 拼装需要 worker_name。"""
    from repository.worker import WorkerRepository
    repo = WorkerRepository.__new__(WorkerRepository)
    repo.session = MagicMock()
    repo.list_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_shipments():
    from repository.outsource_shipment import OutsourceShipmentRepository
    repo = OutsourceShipmentRepository.__new__(OutsourceShipmentRepository)
    repo.session = MagicMock()
    repo.create = AsyncMock(side_effect=lambda s: s)
    repo.update = AsyncMock(side_effect=lambda s: s)
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_open_by_batch_id = AsyncMock(return_value=None)
    repo.find_open_by_part_company_process = AsyncMock(return_value=None)
    repo.list_reconciliation_for_company = AsyncMock(return_value=[])
    repo.count_reconciliation_for_company = AsyncMock(return_value=0)
    repo.list_in_flight = AsyncMock(return_value=[])
    repo.count_in_flight = AsyncMock(return_value=0)
    return repo


@pytest.fixture
def svc(
    mock_quotes, mock_quote_events, mock_parts, mock_companies,
    mock_processes, mock_customers, mock_shelves, mock_workers,
    mock_part_events, mock_shipments,
) -> OutsourceQuoteService:
    return OutsourceQuoteService(
        quotes=mock_quotes,
        quote_events=mock_quote_events,
        parts=mock_parts,
        companies=mock_companies,
        processes=mock_processes,
        customers=mock_customers,
        shelves=mock_shelves,
        workers=mock_workers,
        part_events=mock_part_events,
        shipments=mock_shipments,
        current_user=None,
    )


# =============================================================================
# 创建
# =============================================================================


class TestCreateQuote:
    pytestmark = pytest.mark.asyncio

    async def test_happy_path(self, svc, mock_quotes, mock_parts, mock_companies, mock_processes, mock_quote_events):
        mock_parts.get_by_id.return_value = _make_part()
        mock_companies.get_by_id.return_value = _make_company()
        mock_processes.get_by_id.return_value = _make_process()
        mock_quotes.get_one_active_for_tuple.return_value = None

        # 让 mock repo.create 给 quote 填上 created_at/updated_at（模拟 DB RETURNING）
        async def _fake_create(q):
            q.created_at = _now()
            q.updated_at = _now()
            return q
        mock_quotes.create = AsyncMock(side_effect=_fake_create)

        req = OutsourceQuoteCreateRequest(
            part_id="100", outsource_company_id="10", process_id="20",
            price=Decimal("18.50"),
            note="测试备注",
        )
        result = await svc.create_quote(req)
        assert result.status == OutsourceQuoteStatus.DRAFT.value
        assert result.price == Decimal("18.50")
        mock_quotes.create.assert_awaited_once()
        # 初始 CREATED 事件 + 任何其他都未写
        assert mock_quote_events.create.await_count == 1

    async def test_part_not_found(self, svc, mock_parts):
        mock_parts.get_by_id.return_value = None
        req = OutsourceQuoteCreateRequest(
            part_id="999", outsource_company_id="10", process_id="20",
            price=Decimal("5.00"),
        )
        with pytest.raises(BizError) as exc:
            await svc.create_quote(req)
        assert exc.value.code == ErrCode.BIZ_PART_NOT_FOUND

    async def test_company_not_found(self, svc, mock_parts, mock_companies):
        mock_parts.get_by_id.return_value = _make_part()
        mock_companies.get_by_id.return_value = None
        req = OutsourceQuoteCreateRequest(
            part_id="100", outsource_company_id="999", process_id="20",
            price=Decimal("5.00"),
        )
        with pytest.raises(BizError) as exc:
            await svc.create_quote(req)
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_FOUND

    async def test_process_wrong_category(self, svc, mock_parts, mock_companies, mock_processes):
        mock_parts.get_by_id.return_value = _make_part()
        mock_companies.get_by_id.return_value = _make_company()
        mock_processes.get_by_id.return_value = _make_process(id=20, code="INHOUSE-1", category="INHOUSE")
        req = OutsourceQuoteCreateRequest(
            part_id="100", outsource_company_id="10", process_id="20",
            price=Decimal("5.00"),
        )
        with pytest.raises(BizError) as exc:
            await svc.create_quote(req)
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS

    async def test_duplicate_tuple(self, svc, mock_parts, mock_companies, mock_processes, mock_quotes):
        mock_parts.get_by_id.return_value = _make_part()
        mock_companies.get_by_id.return_value = _make_company()
        mock_processes.get_by_id.return_value = _make_process()
        mock_quotes.get_one_active_for_tuple.return_value = _make_quote(
            id=999, status=OutsourceQuoteStatus.APPROVED.value,
        )
        req = OutsourceQuoteCreateRequest(
            part_id="100", outsource_company_id="10", process_id="20",
            price=Decimal("5.00"),
        )
        with pytest.raises(BizError) as exc:
            await svc.create_quote(req)
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_DUPLICATE

    async def test_db_integrity_error(self, svc, mock_parts, mock_companies, mock_processes, mock_quotes):
        mock_parts.get_by_id.return_value = _make_part()
        mock_companies.get_by_id.return_value = _make_company()
        mock_processes.get_by_id.return_value = _make_process()
        mock_quotes.get_one_active_for_tuple.return_value = None
        mock_quotes.create = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("orig")))
        req = OutsourceQuoteCreateRequest(
            part_id="100", outsource_company_id="10", process_id="20",
            price=Decimal("5.00"),
        )
        with pytest.raises(BizError) as exc:
            await svc.create_quote(req)
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_DUPLICATE


# =============================================================================
# get_quote
# =============================================================================


class TestGetQuote:
    pytestmark = pytest.mark.asyncio

    async def test_get_happy_path(self, svc, mock_quotes):
        quote = _make_quote()
        quote.version = 3
        mock_quotes.get_by_id.return_value = quote
        mock_parts = svc.parts  # set in fixture
        svc.parts.get_by_id = AsyncMock(return_value=_make_part())
        svc.companies.get_by_id = AsyncMock(return_value=_make_company())
        svc.processes.get_by_id = AsyncMock(return_value=_make_process())
        svc.customers.get_by_id = AsyncMock(return_value=None)

        result = await svc.get_quote("999")
        assert result.id == 999
        assert result.version == 3

    async def test_get_not_found(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = None
        with pytest.raises(BizError) as exc:
            await svc.get_quote("999")
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_NOT_FOUND


# =============================================================================
# update_quote（DRAFT only + 乐观锁）
# =============================================================================


class TestUpdateQuote:
    pytestmark = pytest.mark.asyncio

    async def test_happy_path(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(version=2)
        svc.parts.get_by_id = AsyncMock(return_value=_make_part())
        svc.companies.get_by_id = AsyncMock(return_value=_make_company())
        svc.processes.get_by_id = AsyncMock(return_value=_make_process())
        svc.customers.get_by_id = AsyncMock(return_value=None)

        req = OutsourceQuoteUpdateRequest(
            version=2,
            price=Decimal("99.99"),
            note="更新备注",
        )
        result = await svc.update_quote("999", req)
        assert result.price == Decimal("99.99")

    async def test_non_draft_status(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(
            status=OutsourceQuoteStatus.SUBMITTED.value, version=2,
        )
        req = OutsourceQuoteUpdateRequest(version=2, price=Decimal("99.99"))
        with pytest.raises(BizError) as exc:
            await svc.update_quote("999", req)
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION

    async def test_version_mismatch(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(version=5)
        req = OutsourceQuoteUpdateRequest(version=2, price=Decimal("99.99"))
        with pytest.raises(BizError) as exc:
            await svc.update_quote("999", req)
        assert exc.value.code == ErrCode.BIZ_VERSION_CONFLICT


# =============================================================================
# 状态机转换
# =============================================================================


class TestStateTransitions:
    pytestmark = pytest.mark.asyncio

    async def test_submit_draft_to_submitted(self, svc, mock_quotes):
        q = _make_quote(status=OutsourceQuoteStatus.DRAFT.value)
        mock_quotes.get_by_id.return_value = q
        svc.parts.get_by_id = AsyncMock(return_value=_make_part())
        svc.companies.get_by_id = AsyncMock(return_value=_make_company())
        svc.processes.get_by_id = AsyncMock(return_value=_make_process())
        svc.customers.get_by_id = AsyncMock(return_value=None)

        result = await svc.submit_quote("999")
        assert result.status == OutsourceQuoteStatus.SUBMITTED.value

    async def test_submit_invalid_status(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(
            status=OutsourceQuoteStatus.APPROVED.value,
        )
        with pytest.raises(BizError) as exc:
            await svc.submit_quote("999")
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION

    async def test_approve_happy(self, svc, mock_quotes):
        q = _make_quote(status=OutsourceQuoteStatus.SUBMITTED.value, version=2)
        mock_quotes.get_by_id.return_value = q
        svc.parts.get_by_id = AsyncMock(return_value=_make_part())
        svc.companies.get_by_id = AsyncMock(return_value=_make_company())
        svc.processes.get_by_id = AsyncMock(return_value=_make_process())
        svc.customers.get_by_id = AsyncMock(return_value=None)

        result = await svc.approve_quote("999", OutsourceQuoteApproveRequest(
            version=2, review_note="OK",
        ))
        assert result.status == OutsourceQuoteStatus.APPROVED.value

    async def test_approve_version_mismatch(self, svc, mock_quotes):
        q = _make_quote(status=OutsourceQuoteStatus.SUBMITTED.value, version=5)
        mock_quotes.get_by_id.return_value = q
        with pytest.raises(BizError) as exc:
            await svc.approve_quote("999", OutsourceQuoteApproveRequest(version=2))
        assert exc.value.code == ErrCode.BIZ_VERSION_CONFLICT

    async def test_reject_happy(self, svc, mock_quotes):
        q = _make_quote(status=OutsourceQuoteStatus.SUBMITTED.value, version=2)
        mock_quotes.get_by_id.return_value = q
        svc.parts.get_by_id = AsyncMock(return_value=_make_part())
        svc.companies.get_by_id = AsyncMock(return_value=_make_company())
        svc.processes.get_by_id = AsyncMock(return_value=_make_process())
        svc.customers.get_by_id = AsyncMock(return_value=None)

        result = await svc.reject_quote("999", OutsourceQuoteRejectRequest(
            version=2, review_note="价格偏高",
        ))
        assert result.status == OutsourceQuoteStatus.REJECTED.value


# =============================================================================
# soft_delete
# =============================================================================


class TestSoftDelete:
    pytestmark = pytest.mark.asyncio

    async def test_delete_draft(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(status=OutsourceQuoteStatus.DRAFT.value)
        await svc.soft_delete_quote("999")
        mock_quotes.soft_delete.assert_awaited_once()

    async def test_delete_rejected(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(
            status=OutsourceQuoteStatus.REJECTED.value,
        )
        await svc.soft_delete_quote("999")
        mock_quotes.soft_delete.assert_awaited_once()

    async def test_cannot_delete_submitted(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(
            status=OutsourceQuoteStatus.SUBMITTED.value,
        )
        with pytest.raises(BizError) as exc:
            await svc.soft_delete_quote("999")
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION

    async def test_cannot_delete_approved(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(
            status=OutsourceQuoteStatus.APPROVED.value,
        )
        with pytest.raises(BizError) as exc:
            await svc.soft_delete_quote("999")
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION

    async def test_cannot_delete_used(self, svc, mock_quotes):
        mock_quotes.get_by_id.return_value = _make_quote(
            status=OutsourceQuoteStatus.USED.value,
        )
        with pytest.raises(BizError) as exc:
            await svc.soft_delete_quote("999")
        assert exc.value.code == ErrCode.BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION


# =============================================================================
# PartService 注入依赖 (smoke check)
# =============================================================================


class TestApiRouterImports:
    """冒烟测试：API router 可被 import 且端点路径正确。"""

    def test_router_paths(self):
        from api.v1.outsource_quote import read_router, write_router
        # prefix="/outsource-quotes" — route 路径是 prefix + sub-path
        read_paths = sorted(r.path for r in read_router.routes)
        write_paths = sorted(r.path for r in write_router.routes)
        # 读路由（含 prefix）
        assert "/outsource-quotes" in read_paths
        assert "/outsource-quotes/approved-for-send" in read_paths
        assert "/outsource-quotes/{quote_id}" in read_paths
        # 写路由
        assert "/outsource-quotes/{quote_id}/update" in write_paths
        assert "/outsource-quotes/{quote_id}/submit" in write_paths
        assert "/outsource-quotes/{quote_id}/approve" in write_paths
        assert "/outsource-quotes/{quote_id}/reject" in write_paths
        assert "/outsource-quotes/{quote_id}/soft-delete" in write_paths


# =============================================================================
# 同步 TPartEvent 写入（2026-07-16 新增）
# =============================================================================


class TestPartEventSyncWrites:
    """create_quote / approve_quote 必须同步写 TPartEvent 一行，
    让 PartDetail 历史时间线展示报价生命周期。
    """
    pytestmark = pytest.mark.asyncio

    async def test_create_quote_writes_part_event_quote_created(
        self, svc, mock_quotes, mock_parts, mock_companies, mock_processes,
        mock_quote_events, mock_part_events,
    ):
        from schema.outsource_quote import OutsourceQuoteCreateRequest
        from decimal import Decimal

        mock_parts.get_by_id.return_value = _make_part()
        mock_companies.get_by_id.return_value = _make_company()
        mock_processes.get_by_id.return_value = _make_process()
        mock_quotes.get_one_active_for_tuple.return_value = None

        # 让 mock repo.create 给 quote 填上 created_at/updated_at（模拟 DB RETURNING）
        async def _fake_create(q):
            q.created_at = _now()
            q.updated_at = _now()
            return q
        mock_quotes.create = AsyncMock(side_effect=_fake_create)

        data = OutsourceQuoteCreateRequest(
            part_id="123456",
            outsource_company_id="200",
            process_id="300",
            price=Decimal("100.00"),
        )
        await svc.create_quote(data)

        # TOutsourceQuoteEvent（CREATED）+ TPartEvent（QUOTE_CREATED）共 2 次
        assert mock_quote_events.create.await_count == 1
        assert mock_part_events.create.await_count == 1
        # 第 2 次写的是 TPartEvent
        part_event = mock_part_events.create.await_args.args[0]
        assert part_event.event_type == "QUOTE_CREATED"
        # part_id 是 int(snowflake id), 我们传了 part_id="123456"
        assert part_event.part_id == 123456
        assert "外协公司" in part_event.note
        assert "工序" in part_event.note
        assert "报价:#" in part_event.note

    async def test_approve_quote_writes_part_event_quote_approved(
        self, svc, mock_quotes, mock_companies, mock_processes,
        mock_part_events,
    ):
        from schema.outsource_quote import OutsourceQuoteApproveRequest

        q = _make_quote(
            status=OutsourceQuoteStatus.SUBMITTED.value, version=2,
        )
        mock_quotes.get_by_id.return_value = q
        mock_companies.get_by_id.return_value = _make_company()
        mock_processes.get_by_id.return_value = _make_process()

        data = OutsourceQuoteApproveRequest(version=2, review_note="OK")
        await svc.approve_quote("999", data)

        assert mock_part_events.create.await_count == 1
        part_event = mock_part_events.create.await_args.args[0]
        assert part_event.event_type == "QUOTE_APPROVED"
        assert part_event.part_id == q.part_id
        assert "审批意见:OK" in part_event.note


# =============================================================================
# approved-for-send 500 回归（2026-07-16 修复）
# 根因：service/outsource_quote.py 漏 await self._make_customer_path
# 现象：customer_path 字段是 coroutine，Pydantic 序列化抛 500
# =============================================================================


class TestListApprovedForSend:
    pytestmark = pytest.mark.asyncio

    async def test_customer_path_is_string_not_coroutine(
        self, svc, mock_quotes, mock_companies, mock_processes, mock_customers,
    ):
        """回归：approved-for-send 返回 items 的 customer_path 必须是 str/None。

        2026-07-20 重构后：list_all_approved + list_outsource_sendable(part_ids_in)
        + 批量 list_by_ids，customer_path 走 _preload_customer_cache 纯内存拼接。
        """
        from datetime import date

        # 1 个 APPROVED 报价（part_id=100 / company=10 / process=20）
        quote = _make_quote(status="APPROVED")
        mock_quotes.list_all_approved = AsyncMock(return_value=[quote])

        # 命中的可发送零件（挂 L2 客户 501）
        part = _make_part(id=100, customer_id=501)
        part.status = "PENDING"
        part.location = "OFFICE"
        part.next_process_id = 20
        part.is_urgent = False
        part.planned_delivery_date = date(2026, 7, 20)
        # 2026-07-29 PR-fix-0.2.0 批次化：list_outsource_sendable 现在返回
        # list[tuple[TPartBatch, TPart]]；mock 也返回元组。
        batch = _make_batch(id=999, part_id=100, status="PENDING",
                            location="OFFICE", next_process_id=20, quantity=10)
        svc.parts.count_outsource_sendable = AsyncMock(return_value=1)
        svc.parts.list_outsource_sendable = AsyncMock(return_value=[(batch, part)])

        # 两层客户：list_by_ids 按 frontier 逐层返回
        from model.customer import TCustomer
        parent = TCustomer(id=500, name="法拉电子", parent_id=None)
        child = TCustomer(id=501, name="三厂", parent_id=500)
        mock_customers.list_by_ids = AsyncMock(side_effect=lambda ids: {
            (501,): [child],
            (500,): [parent],
        }.get(tuple(ids), []))

        svc.companies.list_by_ids = AsyncMock(return_value=[_make_company()])
        svc.processes.list_by_ids = AsyncMock(return_value=[_make_process()])

        out = await svc.list_approved_for_send(limit=20, offset=0)

        assert len(out.items) == 1
        for it in out.items:
            assert it.customer_path is None or isinstance(it.customer_path, str), (
                f"customer_path 是 {type(it.customer_path).__name__}，应该是 str/None"
            )
        assert out.items[0].customer_path == "法拉电子 / 三厂"

    async def test_no_get_by_id_in_send_list(
        self, svc, mock_quotes, mock_parts, mock_companies, mock_processes,
    ):
        """N+1 回归：发送列表拼装不得逐条 get_by_id（process/company 走批查）。"""
        from datetime import date

        quotes = [
            _make_quote(id=i, part_id=100 + i, process_id=20, outsource_company_id=10)
            for i in range(5)
        ]
        mock_quotes.list_all_approved = AsyncMock(return_value=quotes)

        parts = []
        batches = []
        for i in range(5):
            p = _make_part(id=100 + i, customer_id=None)
            p.status = "PENDING"
            p.location = "OFFICE"
            p.next_process_id = 20
            p.is_urgent = False
            p.planned_delivery_date = date(2026, 7, 20)
            parts.append(p)
            # 2026-07-29 批次化：mock list_outsource_sendable 返回元组
            b = _make_batch(id=900 + i, part_id=100 + i, status="PENDING",
                            location="OFFICE", next_process_id=20, quantity=10)
            batches.append(b)
        svc.parts.count_outsource_sendable = AsyncMock(return_value=len(parts))
        svc.parts.list_outsource_sendable = AsyncMock(
            return_value=list(zip(batches, parts)),
        )
        svc.processes.list_by_ids = AsyncMock(return_value=[_make_process()])
        svc.companies.list_by_ids = AsyncMock(return_value=[_make_company()])

        out = await svc.list_approved_for_send(limit=50, offset=0)

        assert len(out.items) == 5
        assert svc.processes.get_by_id.await_count == 0
        assert svc.companies.get_by_id.await_count == 0
        assert svc.parts.get_by_id.await_count == 0
        # process/company 各只批查一次
        svc.processes.list_by_ids.assert_awaited_once()
        svc.companies.list_by_ids.assert_awaited_once()


class TestListQuotablePartsForPicker:
    """2026-07-29 PR-fix-0.2.0 hotfix 回归测试。

    上轮 commit 把 repository 改为返回 list[tuple[TPartBatch, TPart]]，但 service
    的 _to_part_list_items 在批查（customer / worker / shelf / process）那里
    仍 `for p in rows: p.customer_id` 把 tuple 当 TPart 用 → 500。
    本测试锁住 picker 的 batch-tuple 契约：rows 必须是 tuple，response 里 batch
    字段必须被填进 PartListItem。
    """
    pytestmark = pytest.mark.asyncio

    async def test_picker_unpacks_batch_tuples(
        self, svc, mock_parts, mock_customers, mock_processes,
        mock_shelves, mock_workers,
    ):
        """PR-fix-0.2.0 hotfix 回归：rows 是 list[tuple[TPartBatch, TPart]] 时，
        _to_part_list_items 必须立刻解包，不能再 for p in rows: p.customer_id。
        """
        from repository.shelf_process import ShelfProcessRepository

        # 1. 构造一个 (batch, part) 元组 —— 这就是当前 repo 的 contract
        batch = _make_batch(
            id=999, part_id=100, batch_no=2, quantity=20,
            status="IN_PROCESS", location="PRODUCTION_SHELF",
            current_holder_id=10, next_process_id=20,
        )
        part = _make_part(id=100, customer_id=501)
        part.status = "IN_PROCESS"
        part.location = "PRODUCTION_SHELF"
        part.current_holder_id = 10
        part.next_process_id = 20

        # 关键：mock 返回 [(batch, part)] 而不是 [part]
        mock_parts.list_quotable_for_outsource_quote = AsyncMock(
            return_value=[(batch, part)],
        )

        # 2. 下游批查一律返回空（避免引入额外 mock 复杂度）
        mock_customers.list_by_ids = AsyncMock(return_value=[])
        mock_processes.list_by_ids = AsyncMock(return_value=[])
        mock_shelves.list_by_ids = AsyncMock(return_value=[])
        mock_workers.list_by_ids = AsyncMock(return_value=[])

        # 3. shelf_processes 入参只需要 list_shelf_ids_with_process_category 一个方法
        shelf_process_repo = ShelfProcessRepository.__new__(ShelfProcessRepository)
        shelf_process_repo.list_shelf_ids_with_process_category = AsyncMock(
            return_value=[10],   # 一个绑了 OUTSOURCE 工序的货架
        )

        # 4. 调用 picker —— 不应抛 AttributeError
        items = await svc.list_quotable_parts_for_picker(
            keyword=None, limit=500, shelf_processes=shelf_process_repo,
        )

        # 5. 验证：返回 PartListItem，batch 字段被填上
        assert isinstance(items, list)
        assert len(items) == 1
        item = items[0]
        # IdStrNonNull 在 Python 里是 int；JSON 序列化时变 str。
        assert item.id == 100
        # IdStr（可空外键）同理；PartListItem.batch_id 是 IdStr（可空）。
        assert item.batch_id == 999
        assert item.batch_no == 2
        assert item.batch_quantity == 20


class TestListQuotesNoN1:
    pytestmark = pytest.mark.asyncio

    async def test_list_quotes_uses_batch_not_get_by_id(
        self, svc, mock_quotes, mock_parts, mock_companies, mock_processes,
    ):
        """报价一览序列化走 _to_out_many 批查，禁止逐行 get_by_id。"""
        from schema.outsource_quote import OutsourceQuoteListQuery

        q1 = _make_quote(id=1, part_id=100)
        q2 = _make_quote(id=2, part_id=101)
        mock_quotes.list_with_filters = AsyncMock(return_value=[q1, q2])
        mock_quotes.count_with_filters = AsyncMock(return_value=2)

        p1 = _make_part(id=100, customer_id=None)
        p2 = _make_part(id=101, customer_id=None)
        svc.parts.list_by_ids = AsyncMock(return_value=[p1, p2])
        svc.companies.list_by_ids = AsyncMock(return_value=[_make_company()])
        svc.processes.list_by_ids = AsyncMock(return_value=[_make_process()])

        out = await svc.list_quotes(OutsourceQuoteListQuery())

        assert len(out.items) == 2
        assert out.total == 2
        svc.parts.list_by_ids.assert_awaited_once()
        assert svc.parts.get_by_id.await_count == 0
        assert svc.companies.get_by_id.await_count == 0
        assert svc.processes.get_by_id.await_count == 0



# =============================================================================
# 报价列表 statuses[] 多选透传（2026-07-16 修复）
# =============================================================================


class TestSearchQuotesStatusesMulti:
    pytestmark = pytest.mark.asyncio

    async def test_keyword_passed_through_to_part_repo(
        self, svc, mock_quotes, mock_parts,
    ):
        from schema.outsource_quote import OutsourceQuoteListQuery
        from model.enums import OutsourceQuoteStatus

        # keyword 非空 → _resolve_part_ids 先调 part_repo.count_with_filters；
        # 返回 0 → 无匹配零件 → list_quotes 直接短路返回空（不再 N+1 逐 part 查报价）。
        svc.parts.count_with_filters = AsyncMock(return_value=0)

        q = OutsourceQuoteListQuery(
            statuses=[OutsourceQuoteStatus.DRAFT, OutsourceQuoteStatus.APPROVED],
            keyword="NOMATCH",
        )
        out = await svc.list_quotes(q)
        assert out.items == []
        assert out.total == 0
        # keyword 已传给 part_repo（_resolve_part_ids 路径）
        call_kwargs = svc.parts.count_with_filters.call_args.kwargs
        assert call_kwargs.get("keyword") == "NOMATCH"


# =============================================================================
# 对账页更新已迁移至 shipment（2026-07-30）；旧 reconcile_update_quote 单元测试删除
# =============================================================================


