"""外协公司 (OutsourceCompany) service 单元测试。

mirror tests/unit/test_customer_service.py 与 tests/unit/test_work_type_process_service.py 的风格。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.outsource_company import TOutsourceCompany
from model.process import TProcess
from repository.outsource_company import OutsourceCompanyRepository
from repository.outsource_company_process import OutsourceCompanyProcessRepository
from repository.process import ProcessRepository
from schema.outsource_company import (
    OutsourceCompanyCreateRequest,
    OutsourceCompanyUpdateRequest,
    SetOutsourceCompanyProcessRequest,
)
from service.outsource_company import OutsourceCompanyService

pytestmark = pytest.mark.asyncio


# =============================================================================
# Fixtures & factories
# =============================================================================
def _now() -> datetime:
    return datetime(2026, 1, 1)


def _make_company(
    id: int = 1,
    name: str = "测试外协A",
    is_active: bool = True,
) -> TOutsourceCompany:
    c = TOutsourceCompany(
        id=id, name=name, is_active=is_active,
        contact_name="张三", contact_phone="13800001111", address="福州",
    )
    c.created_at = _now()
    c.updated_at = _now()
    c.deleted_at = None
    return c


def _make_process(
    id: int,
    code: str,
    category: str = "OUTSOURCE",
) -> TProcess:
    p = TProcess(
        id=id, code=code, name=f"{code}名称",
        category=category, sort_order=0,
    )
    p.created_at = _now()
    p.updated_at = _now()
    p.description = None
    p.deleted_at = None
    return p


@pytest.fixture
def mock_companies() -> OutsourceCompanyRepository:
    repo = OutsourceCompanyRepository.__new__(OutsourceCompanyRepository)
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    repo.soft_delete = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_name = AsyncMock(return_value=None)
    repo.list_by_ids = AsyncMock(return_value=[])
    repo.list_with_filters = AsyncMock(return_value=[])
    repo.count_with_filters = AsyncMock(return_value=0)
    return repo


@pytest.fixture
def mock_junction() -> OutsourceCompanyProcessRepository:
    repo = OutsourceCompanyProcessRepository.__new__(OutsourceCompanyProcessRepository)
    repo.create = AsyncMock(side_effect=lambda row: row)
    repo.list_by_outsource_company = AsyncMock(return_value=[])
    repo.list_process_ids_by_outsource_company = AsyncMock(return_value=[])
    repo.list_companys_by_process = AsyncMock(return_value=[])
    repo.delete_by_outsource_company = AsyncMock()
    return repo


@pytest.fixture
def mock_processes() -> ProcessRepository:
    repo = ProcessRepository.__new__(ProcessRepository)
    repo.list_by_ids = AsyncMock(return_value=[])
    repo.get_by_id = AsyncMock()
    return repo


@pytest.fixture
def service(
    mock_companies: OutsourceCompanyRepository,
    mock_junction: OutsourceCompanyProcessRepository,
    mock_processes: ProcessRepository,
) -> OutsourceCompanyService:
    return OutsourceCompanyService(
        companies=mock_companies,
        junction=mock_junction,
        processes=mock_processes,
    )


# =============================================================================
# list_companies
# =============================================================================
class TestListCompanies:
    async def test_returns_items_and_total(
        self, service: OutsourceCompanyService, mock_companies,
    ) -> None:
        c = _make_company(id=1, name="A")
        mock_companies.list_with_filters = AsyncMock(return_value=[c])
        mock_companies.count_with_filters = AsyncMock(return_value=1)

        out = await service.list_companies.__wrapped__(service) if False else None
        from schema.outsource_company import OutsourceCompanyListQuery
        result = await service.list_companies(OutsourceCompanyListQuery(
            name_like="A", is_active=True, limit=10, offset=0,
        ))
        assert result.items[0].name == "A"
        assert result.total == 1
        assert result.limit == 10
        assert result.offset == 0


# =============================================================================
# get_company
# =============================================================================
class TestGetCompany:
    async def test_returns_with_processes(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        c = _make_company(id=100, name="X")
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        mock_processes.list_by_ids = AsyncMock(return_value=[])

        out = await service.get_company("100")
        assert out.id == 100
        assert out.name == "X"
        assert out.processes == []

    async def test_not_found_raises(
        self, service: OutsourceCompanyService, mock_companies,
    ) -> None:
        mock_companies.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(BizError) as exc_info:
            await service.get_company("999")
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_NOT_FOUND
        assert exc_info.value.http_status == http_status.HTTP_404_NOT_FOUND


# ==========================================================================================
# create_company
# ==========================================================================================
class TestCreateCompany:
    async def test_happy_path(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        mock_companies.get_by_name = AsyncMock(return_value=None)
        captured: list[TOutsourceCompany] = []

        async def _create(c):
            c.created_at = _now()
            c.updated_at = _now()
            captured.append(c)
            return c
        mock_companies.create = AsyncMock(side_effect=_create)

        async def _get_by_id(cid, **_):
            return next((c for c in captured if c.id == cid), None)
        mock_companies.get_by_id = AsyncMock(side_effect=_get_by_id)

        mock_processes.list_by_ids = AsyncMock(return_value=[])

        data = OutsourceCompanyCreateRequest(
            name="新外协A", contact_name="张三", contact_phone="13800001111",
            address="福州", is_active=True, process_ids=[],
        )
        out = await service.create_company(data)
        assert out.name == "新外协A"
        assert mock_companies.create.await_count == 1

    async def test_duplicate_name_raises(
        self, service: OutsourceCompanyService, mock_companies,
    ) -> None:
        mock_companies.get_by_name = AsyncMock(
            return_value=_make_company(id=1, name="同名"),
        )
        data = OutsourceCompanyCreateRequest(name="同名")
        with pytest.raises(BizError) as exc_info:
            await service.create_company(data)
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE
        assert exc_info.value.http_status == http_status.HTTP_409_CONFLICT

    async def test_integrity_error_caught(
        self, service: OutsourceCompanyService, mock_companies,
    ) -> None:
        mock_companies.get_by_name = AsyncMock(return_value=None)
        mock_companies.create = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception()))
        data = OutsourceCompanyCreateRequest(name="Z")
        with pytest.raises(BizError) as exc_info:
            await service.create_company(data)
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE


# ==========================================================================================
# update_company
# ==========================================================================================
class TestUpdateCompany:
    async def test_partial_update(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        c = _make_company(id=5, name="OLD")
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        mock_processes.list_by_ids = AsyncMock(return_value=[])

        out = await service.update_company("5", OutsourceCompanyUpdateRequest(
            contact_phone="13900000000", is_active=False,
        ))
        assert out.contact_phone == "13900000000"
        assert out.is_active is False
        assert out.name == "OLD"  # 没动

    async def test_duplicate_name_on_rename_raises(
        self, service: OutsourceCompanyService, mock_companies,
    ) -> None:
        c = _make_company(id=5, name="OLD")
        mock_companies.get_by_id = AsyncMock(return_value=c)
        # 假设已被另一条占用
        mock_companies.get_by_name = AsyncMock(
            return_value=_make_company(id=6, name="NEW"),
        )
        with pytest.raises(BizError) as exc_info:
            await service.update_company("5", OutsourceCompanyUpdateRequest(name="NEW"))
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_DUPLICATE


# ==========================================================================================
# soft_delete_company
# ==========================================================================================
class TestSoftDeleteCompany:
    async def test_happy_path(
        self, service: OutsourceCompanyService, mock_companies, mock_junction,
    ) -> None:
        c = _make_company(id=10)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        await service.soft_delete_company("10")
        mock_companies.soft_delete.assert_awaited_once()

    async def test_in_use_when_mappings_exist_raises(
        self, service: OutsourceCompanyService, mock_companies, mock_junction,
    ) -> None:
        c = _make_company(id=10)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        # 模拟还有 1 条映射
        from model.outsource_company_process import TOutsourceCompanyProcess
        row = TOutsourceCompanyProcess(
            id=999, outsource_company_id=10, process_id=1, sort_order=0,
        )
        row.created_at = _now(); row.updated_at = _now(); row.deleted_at = None
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[row])

        with pytest.raises(BizError) as exc_info:
            await service.soft_delete_company("10")
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_IN_USE


# ==========================================================================================
# set_outsource_company_processes
# ==========================================================================================
class TestSetCompanyProcesses:
    async def test_replaces_existing(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        c = _make_company(id=20)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        mock_processes.list_by_ids = AsyncMock(side_effect=lambda ids, **kw: [
            _make_process(i, f"P{i}") for i in ids
        ])

        await service.set_outsource_company_processes(
            "20", SetOutsourceCompanyProcessRequest(process_ids=["10", "20"]),
        )
        mock_junction.delete_by_outsource_company.assert_awaited_once_with(20)
        assert mock_junction.create.await_count == 2

    async def test_invalid_process_id_raises(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        c = _make_company(id=20)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        mock_processes.list_by_ids = AsyncMock(return_value=[])  # 找不到

        with pytest.raises(BizError) as exc_info:
            await service.set_outsource_company_processes(
                "20", SetOutsourceCompanyProcessRequest(process_ids=["999"]),
            )
        assert exc_info.value.code == ErrCode.BIZ_PROCESS_NOT_FOUND

    async def test_wrong_category_raises(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        c = _make_company(id=20)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        # 传入 INHOUSE 工序 → 应被拒
        mock_processes.list_by_ids = AsyncMock(return_value=[
            _make_process(10, "车", category="INHOUSE"),
        ])
        with pytest.raises(BizError) as exc_info:
            await service.set_outsource_company_processes(
                "20", SetOutsourceCompanyProcessRequest(process_ids=["10"]),
            )
        assert exc_info.value.code == ErrCode.BIZ_OUTSOURCE_COMPANY_BAD_PROCESS

    async def test_dedupes_process_ids(
        self, service: OutsourceCompanyService, mock_companies, mock_junction, mock_processes,
    ) -> None:
        c = _make_company(id=20)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])
        mock_processes.list_by_ids = AsyncMock(side_effect=lambda ids, **kw: [
            _make_process(i, f"P{i}") for i in ids
        ])
        await service.set_outsource_company_processes(
            "20", SetOutsourceCompanyProcessRequest(
                process_ids=["10", "20", "10", "30", "20"],
            ),
        )
        assert mock_junction.create.await_count == 3  # 去重后 3 条

    async def test_invalid_snowflake_id_raises(
        self, service: OutsourceCompanyService, mock_companies, mock_junction,
    ) -> None:
        """process_ids 元素非数字字符串 → BIZ_INVALID_VALUE 400。"""
        c = _make_company(id=20)
        mock_companies.get_by_id = AsyncMock(return_value=c)
        mock_junction.list_by_outsource_company = AsyncMock(return_value=[])

        with pytest.raises(BizError) as exc_info:
            await service.set_outsource_company_processes(
                "20", SetOutsourceCompanyProcessRequest(process_ids=["abc"]),
            )
        assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE


# ==========================================================================================
# list_companies_for_process
# ==========================================================================================
class TestListCompaniesForProcess:
    async def test_returns_active_only(
        self, service: OutsourceCompanyService, mock_junction, mock_companies,
    ) -> None:
        from model.outsource_company_process import TOutsourceCompanyProcess
        active = _make_company(id=1, name="A", is_active=True)
        inactive = _make_company(id=2, name="B", is_active=False)
        rows = [
            TOutsourceCompanyProcess(
                id=10, outsource_company_id=1, process_id=99, sort_order=0,
            ),
            TOutsourceCompanyProcess(
                id=11, outsource_company_id=2, process_id=99, sort_order=1,
            ),
        ]
        for r in rows:
            r.created_at = _now(); r.updated_at = _now(); r.deleted_at = None
        mock_junction.list_companys_by_process = AsyncMock(return_value=rows)
        mock_companies.list_by_ids = AsyncMock(return_value=[active, inactive])

        out = await service.list_companies_for_process(99)
        assert len(out) == 1
        assert out[0].name == "A"
        assert out[0].is_active is True