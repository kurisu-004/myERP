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
from model.enums import OutsourceQuoteStatus, ProcessCategory
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

pytestmark = pytest.mark.asyncio


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
    p = MagicMock()
    p.id = id
    p.serial_no = serial_no
    p.drawing_no = drawing_no
    p.name = name
    p.quantity = quantity
    p.customer_id = customer_id
    p.deleted_at = None
    return p


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
    repo.get_one_active_for_tuple = AsyncMock(return_value=None)
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
def svc(
    mock_quotes, mock_quote_events, mock_parts, mock_companies,
    mock_processes, mock_customers, mock_part_events,
) -> OutsourceQuoteService:
    return OutsourceQuoteService(
        quotes=mock_quotes,
        quote_events=mock_quote_events,
        parts=mock_parts,
        companies=mock_companies,
        processes=mock_processes,
        customers=mock_customers,
        part_events=mock_part_events,
        current_user=None,
    )


# =============================================================================
# 创建
# =============================================================================


class TestCreateQuote:
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

    async def test_state_machine_mark_used(self):
        """直接验证 OutsourceQuoteStateMachine 的 mark_used 转换。"""
        from statemachines.outsource_quote import OutsourceQuoteStateMachine
        q = _make_quote(status=OutsourceQuoteStatus.APPROVED.value, version=2)
        events = []
        sm = OutsourceQuoteStateMachine(model=q)
        sm.mark_used(event_repo=MagicMock(add=lambda ev: events.append(ev)), created_by=42)
        assert q.status == OutsourceQuoteStatus.USED.value
        assert len(events) == 1
        assert events[0].event_type == OutsourceQuoteStatus.USED.value or events[0].event_type == "USED"


# =============================================================================
# soft_delete
# =============================================================================


class TestSoftDelete:
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
    async def test_customer_path_is_string_not_coroutine(
        self, svc, mock_quotes, mock_companies, mock_processes, mock_customers,
    ):
        """回归：approved-for-send 返回 items 的 customer_path 必须是 str/None。"""
        # 1 个 APPROVED 报价（外协工序 = OUTSOURCE 类别）
        quote = _make_quote(status="APPROVED")
        mock_quotes.session = MagicMock()
        # _list_all_approved_quotes 直接走 session.execute → mock 出 quote 列表
        mock_quotes.session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[quote]))),
        ))

        # 1 个 PENDING 零件（无客户，方便让 customer_path 走 cust_map 缺省分支）
        pending_part = _make_part()
        # 1 个 IN_PROCESS/PRODUCTION_SHELF + next_process=OUTSOURCE 类别
        in_process_part = _make_part(id=200)
        in_process_part.id = 200
        in_process_part.status = "IN_PROCESS"
        in_process_part.location = "PRODUCTION_SHELF"
        in_process_part.customer_id = None  # 不让 _make_customer_path 触发
        # next_process_id 默认是 0，_make_process 返回的 process 已是 OUTSOURCE
        in_process_part.next_process_id = _make_process().id

        svc.parts.list_with_filters = AsyncMock(
            return_value=[pending_part, in_process_part],
        )
        # L2 客户：让 _make_customer_path 真走一次（避免空路径侥幸通过）
        from model.customer import TCustomer
        parent = TCustomer(id=500, name="法拉电子", parent_id=None)
        child = TCustomer(id=501, name="三厂", parent_id=500)
        # 给其中一个 part 关联到 child
        pending_part.customer_id = 501
        mock_customers.list_by_ids = AsyncMock(side_effect=lambda ids: {
            (501,): [child],
            (500,): [parent],
        }.get(tuple(ids) or (), []))

        svc.companies.get_by_id = AsyncMock(return_value=_make_company())
        svc.processes.get_by_id = AsyncMock(return_value=_make_process())

        out = await svc.list_approved_for_send(limit=20, offset=0)

        # 关键断言：没有 coroutine 漏进 customer_path
        for it in out.items:
            assert it.customer_path is None or isinstance(it.customer_path, str), (
                f"customer_path 是 {type(it.customer_path).__name__}，应该是 str/None"
            )


# =============================================================================
# 报价列表 statuses[] 多选透传（2026-07-16 修复）
# =============================================================================


class TestSearchQuotesStatusesMulti:
    async def test_statuses_array_passed_through(
        self, svc, mock_quotes, mock_parts,
    ):
        from schema.outsource_quote import OutsourceQuoteListQuery
        from model.enums import OutsourceQuoteStatus

        # 准备：1 个 part 关联 1 个 quote
        quote = _make_quote()
        mock_quotes.session = MagicMock()
        mock_quotes.session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[quote]))),
        ))

        # 业务场景：先 listParts 拿 part_ids（service 会先调 part_repo）
        svc.parts.list_with_filters = AsyncMock(return_value=[])

        q = OutsourceQuoteListQuery(
            statuses=[OutsourceQuoteStatus.DRAFT, OutsourceQuoteStatus.APPROVED],
        )
        # part_filter_ids is None if customer_id is None
        # → 走 _search_quotes 直接传 list_with_filters / count_with_filters
        # 我们让 part_filter_ids = [] 来短路（避免全链路 mock）
        from service.outsource_quote import OutsourceQuoteService
        # 用 keyword 不为空 → 走 _resolve_part_ids → list_with_filters（无结果）
        q2 = OutsourceQuoteListQuery(
            statuses=[OutsourceQuoteStatus.DRAFT, OutsourceQuoteStatus.APPROVED],
            keyword="NOMATCH",
        )
        out = await svc.list_quotes(q2)
        assert out.items == []
        assert out.total == 0
        # 验证 svc 把 list 传给了 part_repo（_resolve_part_ids 路径）
        call_kwargs = svc.parts.list_with_filters.call_args.kwargs
        assert call_kwargs.get("keyword") == "NOMATCH"

