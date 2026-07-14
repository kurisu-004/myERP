"""ApplicantService 单元测试。

- get_or_create：单条幂等创建 + race 兜底（沿用已有逻辑）
- bulk_get_or_create：批量去重 + L1/L2 自动上溯 + 空列表短路
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.applicant import TApplicant
from model.customer import TCustomer
from repository.applicant import ApplicantRepository
from repository.customer import CustomerRepository
from schema.applicant import BulkApplicantItem
from service.applicant import ApplicantService

pytestmark = pytest.mark.asyncio


# ======================================================================
# fixtures
# ======================================================================


def _make_applicant(
    id: int, name: str, customer_id: int,
) -> TApplicant:
    return TApplicant(
        id=id, name=name, customer_id=customer_id,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )


def _make_customer(
    id: int, name: str, parent_id: int | None = None,
) -> TCustomer:
    return TCustomer(
        id=id, name=name, parent_id=parent_id,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )


@pytest.fixture
def mock_applicants() -> ApplicantRepository:
    repo = ApplicantRepository.__new__(ApplicantRepository)
    repo.create = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock()
    repo.find_by_name_and_customer = AsyncMock(return_value=None)
    repo.update = AsyncMock()
    repo.soft_delete = AsyncMock()
    return repo


@pytest.fixture
def mock_customers() -> CustomerRepository:
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.get_by_id = AsyncMock()
    repo.list_by_ids = AsyncMock(return_value=[])
    repo.list_children = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def service(
    mock_applicants: ApplicantRepository,
    mock_customers: CustomerRepository,
) -> ApplicantService:
    return ApplicantService(applicants=mock_applicants, customers=mock_customers)


# ======================================================================
# get_or_create 既有逻辑（保持工作）
# ======================================================================


class TestGetOrCreate:
    async def test_returns_existing_applicant(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        existing = _make_applicant(id=9001, name="张三", customer_id=1)
        mock_customers.get_by_id.return_value = _make_customer(id=1, name="法拉电子")
        mock_applicants.find_by_name_and_customer.return_value = existing

        result = await service.get_or_create("张三", 1)

        # IdStrNonNull 在 Python 端是 int（只在 JSON 序列化时转 str）
        assert result.id == 9001
        assert result.name == "张三"
        mock_applicants.create.assert_not_awaited()

    async def test_creates_new_applicant_when_missing(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        fara_root = _make_customer(id=1, name="法拉电子")
        mock_customers.get_by_id.return_value = fara_root
        mock_applicants.find_by_name_and_customer.return_value = None

        # 模拟 DB server_default：给新对象补上 created_at / updated_at
        async def fake_create(applicant: TApplicant) -> TApplicant:
            applicant.created_at = datetime(2025, 1, 1)
            applicant.updated_at = datetime(2025, 1, 1)
            return applicant
        mock_applicants.create.side_effect = fake_create

        result = await service.get_or_create("张三", 1)

        assert result.name == "张三"
        assert result.customer_id == 1
        mock_applicants.create.assert_awaited_once()

    async def test_rejects_l2_customer(
        self,
        service: ApplicantService,
        mock_customers: CustomerRepository,
    ) -> None:
        mock_customers.get_by_id.return_value = _make_customer(
            id=2, name="一厂", parent_id=1,
        )
        with pytest.raises(BizError) as exc:
            await service.get_or_create("张三", 2)
        assert exc.value.code == ErrCode.BIZ_APPLICANT_BAD_CUSTOMER

    async def test_handles_race_via_integrity_error(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        # 第一次 find → None → 走 create
        # create 抛 IntegrityError（race） → 第二次 find → 命中
        existing = _make_applicant(id=9001, name="张三", customer_id=1)
        mock_customers.get_by_id.return_value = _make_customer(id=1, name="法拉电子")
        mock_applicants.find_by_name_and_customer.side_effect = [None, existing]
        mock_applicants.create.side_effect = IntegrityError("stmt", "params", "orig")

        result = await service.get_or_create("张三", 1)
        assert result.id == 9001
        assert mock_applicants.find_by_name_and_customer.await_count == 2

    async def test_rejects_empty_name(
        self,
        service: ApplicantService,
        mock_customers: CustomerRepository,
    ) -> None:
        mock_customers.get_by_id.return_value = _make_customer(id=1, name="法拉电子")
        with pytest.raises(BizError) as exc:
            await service.get_or_create("   ", 1)
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE


# ======================================================================
# bulk_get_or_create（新功能）
# ======================================================================


class TestBulkGetOrCreate:
    async def test_empty_list_returns_empty(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        result = await service.bulk_get_or_create([])
        assert result == []
        mock_applicants.create.assert_not_awaited()
        mock_customers.get_by_id.assert_not_awaited()

    async def test_dedupes_unique_names(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        # 5 行只有 2 个不同姓名 → 期望 get_or_create 调用 2 次
        fara_root = _make_customer(id=1, name="法拉电子")
        yichang = _make_customer(id=2, name="一厂", parent_id=1)
        mock_customers.get_by_id.side_effect = lambda cid: yichang if cid == 2 else fara_root

        # get_or_create 第一次返回 existing（命中），第二次返回 existing（命中）
        zhangsan = _make_applicant(id=100, name="张三", customer_id=1)
        lisi = _make_applicant(id=101, name="李四", customer_id=1)
        mock_applicants.find_by_name_and_customer.side_effect = [zhangsan, lisi]

        items = [
            BulkApplicantItem(name="张三", customer_id="2"),
            BulkApplicantItem(name="李四", customer_id="2"),
            BulkApplicantItem(name="张三", customer_id="2"),  # 重复
            BulkApplicantItem(name="李四", customer_id="2"),  # 重复
            BulkApplicantItem(name="张三", customer_id="2"),  # 重复
        ]
        result = await service.bulk_get_or_create(items)

        assert len(result) == 2
        names = {r.name for r in result}
        assert names == {"张三", "李四"}
        # 每个返回的 customer_id 都是 L1 根 id（int in Python）
        for r in result:
            assert r.customer_id == 1
        # get_or_create 实际只调 2 次（dedupe 后）
        assert mock_applicants.find_by_name_and_customer.await_count == 2

    async def test_l2_customer_walks_to_l1_root(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        # L2 入参 → 内部上溯到 L1 → get_or_create 拿 L1 customer_id
        fara_root = _make_customer(id=1, name="法拉电子")
        yichang = _make_customer(id=2, name="一厂", parent_id=1)
        # 用 side_effect 让 L1/L2 校验走对 customer
        mock_customers.get_by_id.side_effect = lambda cid: (
            yichang if cid == 2 else fara_root
        )

        # find_by_name_and_customer 返回 None → 走 create → 返新 applicant
        mock_applicants.find_by_name_and_customer.return_value = None

        async def fake_create(applicant: TApplicant) -> TApplicant:
            applicant.created_at = datetime(2025, 1, 1)
            applicant.updated_at = datetime(2025, 1, 1)
            return applicant
        mock_applicants.create.side_effect = fake_create

        items = [BulkApplicantItem(name="张三", customer_id="2")]  # 2 = L2 一厂
        result = await service.bulk_get_or_create(items)

        assert len(result) == 1
        assert result[0].name == "张三"
        assert result[0].customer_id == 1  # L1 根 id
        # find_by_name_and_customer 被调一次（走 L1 customer_id=1）
        assert mock_applicants.find_by_name_and_customer.await_count == 1

    async def test_l1_customer_passes_through(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        fara_root = _make_customer(id=1, name="法拉电子")
        mock_customers.get_by_id.return_value = fara_root

        # get_or_create 内部也会再调一次 get_by_id 校验 L1
        zhangsan = _make_applicant(id=100, name="张三", customer_id=1)
        mock_applicants.find_by_name_and_customer.return_value = zhangsan

        items = [BulkApplicantItem(name="张三", customer_id="1")]
        result = await service.bulk_get_or_create(items)

        assert len(result) == 1
        assert result[0].customer_id == 1

    async def test_separate_l1_roots_create_separate_applicants(
        self,
        service: ApplicantService,
        mock_applicants: ApplicantRepository,
        mock_customers: CustomerRepository,
    ) -> None:
        # 同一个姓名在不同 L1 根下 → 应该是两个不同的申请人
        fara_root = _make_customer(id=1, name="法拉电子")
        fara_yichang = _make_customer(id=2, name="一厂", parent_id=1)
        luda_root = _make_customer(id=10, name="路达")
        luda_yichang = _make_customer(id=11, name="一厂", parent_id=10)

        def get_cust(cid: int):
            return {1: fara_root, 2: fara_yichang, 10: luda_root, 11: luda_yichang}.get(cid)
        mock_customers.get_by_id.side_effect = get_cust

        fara_zs = _make_applicant(id=100, name="张三", customer_id=1)
        luda_zs = _make_applicant(id=200, name="张三", customer_id=10)
        mock_applicants.find_by_name_and_customer.side_effect = [fara_zs, luda_zs]

        items = [
            BulkApplicantItem(name="张三", customer_id="2"),   # 法拉电子/一厂
            BulkApplicantItem(name="张三", customer_id="11"),  # 路达/一厂
        ]
        result = await service.bulk_get_or_create(items)

        assert len(result) == 2
        by_root = {r.customer_id: r.applicant_id for r in result}
        assert by_root[1] == 100  # 法拉电子根
        assert by_root[10] == 200  # 路达根

    async def test_rejects_unknown_customer(
        self,
        service: ApplicantService,
        mock_customers: CustomerRepository,
    ) -> None:
        mock_customers.get_by_id.return_value = None
        with pytest.raises(BizError) as exc:
            await service.bulk_get_or_create(
                [BulkApplicantItem(name="张三", customer_id="999")],
            )
        assert exc.value.code == ErrCode.BIZ_CUSTOMER_NOT_FOUND
        assert exc.value.http_status == http_status.HTTP_404_NOT_FOUND

    async def test_rejects_empty_name(
        self,
        service: ApplicantService,
        mock_customers: CustomerRepository,
    ) -> None:
        # name 在 dedupe 阶段就被拦下，不走 get_or_create
        mock_customers.get_by_id.return_value = _make_customer(id=1, name="法拉电子")
        with pytest.raises(BizError) as exc:
            await service.bulk_get_or_create(
                [BulkApplicantItem(name="   ", customer_id="1")],
            )
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
