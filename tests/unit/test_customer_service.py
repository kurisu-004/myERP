from __future__ import annotations

from datetime import datetime

import pytest
from unittest.mock import AsyncMock
from sqlalchemy.exc import IntegrityError

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.customer import TCustomer
from repository.customer import CustomerRepository
from schema.customer import (
    CustomerCreateRequest,
    CustomerOut,
    CustomerUpdateRequest,
)
from service.customer import CustomerService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_repo() -> CustomerRepository:
    """Create a CustomerRepository with all methods mocked as AsyncMock."""
    repo = CustomerRepository.__new__(CustomerRepository)
    repo.list_all = AsyncMock()
    repo.list_by_ids = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    repo.soft_delete = AsyncMock()
    return repo


@pytest.fixture
def service(mock_repo: CustomerRepository) -> CustomerService:
    """Create a CustomerService backed by the mock repository."""
    return CustomerService(customers=mock_repo)


def _make_customer(
    id: int,
    name: str,
    parent_id: int | None = None,
    serial_prefix: str | None = None,
) -> TCustomer:
    """Factory helper to construct a TCustomer instance without a DB session.

    SQLAlchemy 2.x mapped attributes are descriptors that require
    `_sa_instance_state` on the instance, so we go through the real
    constructor (which initialises the instance state) rather than
    `TCustomer.__new__` + attribute assignment.
    """
    return TCustomer(
        id=id,
        name=name,
        parent_id=parent_id,
        serial_prefix=serial_prefix,
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )


class TestListCustomers:
    """Tests for CustomerService.list_customers."""

    async def test_normal_with_parent_relationship(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """list_customers resolves parent_name from parent records."""
        # ── arrange ──────────────────────────────────────────────
        child = _make_customer(id=3, name="ChildCorp", parent_id=1)
        parent = _make_customer(id=1, name="ParentCorp")
        all_customers = [child, parent]

        mock_repo.list_all.return_value = all_customers
        mock_repo.list_by_ids.return_value = [parent]

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_awaited_once_with([1])

        assert len(result) == 2

        # Child should have parent_name set
        child_out = next(r for r in result if r.id == 3)
        assert child_out.name == "ChildCorp"
        assert child_out.parent_id == 1
        assert child_out.parent_name == "ParentCorp"

        # Parent should have no parent_name
        parent_out = next(r for r in result if r.id == 1)
        assert parent_out.name == "ParentCorp"
        assert parent_out.parent_id is None
        assert parent_out.parent_name is None

    async def test_empty_list(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """list_customers returns [] when no customers exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.list_all.return_value = []
        mock_repo.list_by_ids.return_value = []

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_not_awaited()

        assert result == []

    async def test_no_parent_id(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """Customers without parent_id have parent_name=None and list_by_ids is never called."""
        # ── arrange ──────────────────────────────────────────────
        c1 = _make_customer(id=10, name="Alpha")
        c2 = _make_customer(id=20, name="Beta")
        all_customers = [c1, c2]

        mock_repo.list_all.return_value = all_customers

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_not_awaited()

        assert len(result) == 2
        for r in result:
            assert r.parent_id is None
            assert r.parent_name is None

    async def test_unmatched_parent_id(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """When a parent_id does not match any existing customer, parent_name should be None."""
        # ── arrange ──────────────────────────────────────────────
        orphan = _make_customer(id=42, name="Orphan", parent_id=999)
        all_customers = [orphan]

        mock_repo.list_all.return_value = all_customers
        mock_repo.list_by_ids.return_value = []  # no parent found

        # ── act ──────────────────────────────────────────────────
        result = await service.list_customers()

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_all.assert_awaited_once()
        mock_repo.list_by_ids.assert_awaited_once_with([999])

        assert len(result) == 1
        assert result[0].id == 42
        assert result[0].name == "Orphan"
        assert result[0].parent_id == 999
        assert result[0].parent_name is None


# ======================================================================
# create_customer / update_customer — serial_prefix validation (2026-07-09)
# ======================================================================


class TestCreateCustomerSerialPrefix:
    """serial_prefix 在 create_customer 上的行为。

    - 一级客户（parent_id IS NULL）必填；否则 400 BIZ_INVALID_VALUE。
    - 叶子客户忽略 payload 里的 serial_prefix（永远 None）。
    - 一级客户的 prefix 重复 → DB IntegrityError → 409 BIZ_INVALID_VALUE。
    """

    async def test_root_without_prefix_rejected(
        self,
        service: CustomerService,
    ) -> None:
        """一级客户未指定 serial_prefix → 400。"""
        data = CustomerCreateRequest(
            name="TestRoot", parent_id=None, serial_prefix=None,
        )
        with pytest.raises(BizError) as exc_info:
            await service.create_customer(data)
        assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc_info.value.http_status == http_status.HTTP_400_BAD_REQUEST
        assert "一级客户必须指定序列号前缀" in exc_info.value.message

    async def test_root_with_prefix_persists(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """一级客户带 prefix='G' → 写入 TCustomer.serial_prefix='G'。"""
        data = CustomerCreateRequest(
            name="G Corp", parent_id=None, serial_prefix="G",
        )
        result = await service.create_customer(data)

        # TCustomer 被传给 repo.create；断言属性
        created: TCustomer = mock_repo.create.call_args[0][0]
        assert created.name == "G Corp"
        assert created.parent_id is None
        assert created.serial_prefix == "G"

        # 返回的 CustomerOut 也要带上 prefix
        assert isinstance(result, CustomerOut)
        assert result.serial_prefix == "G"

    async def test_root_lowercase_normalized(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """小写 'g' 由 schema 规范化成 'G'（Pydantic field_validator）。"""
        data = CustomerCreateRequest(
            name="g Corp", parent_id=None, serial_prefix="g",
        )
        await service.create_customer(data)
        created: TCustomer = mock_repo.create.call_args[0][0]
        assert created.serial_prefix == "G"

    async def test_leaf_customer_ignores_prefix(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """叶子客户：payload 传 prefix 也忽略；写入时 prefix 永远 None。"""
        # parent 存在且是 root
        parent = _make_customer(id=99, name="RootX", parent_id=None)
        mock_repo.get_by_id.return_value = parent

        data = CustomerCreateRequest(
            name="Child",
            parent_id="99",
            serial_prefix="Z",  # 应该被忽略
        )
        await service.create_customer(data)
        created: TCustomer = mock_repo.create.call_args[0][0]
        assert created.parent_id == 99
        assert created.serial_prefix is None  # 叶子永远 None

    async def test_root_duplicate_prefix_409(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """DB 部分唯一索引兜底：IntegrityError → 409。"""
        data = CustomerCreateRequest(
            name="Dup", parent_id=None, serial_prefix="F",
        )
        # repo.create 模拟 DB 抛 IntegrityError
        mock_repo.create.side_effect = IntegrityError(
            "INSERT", {}, Exception("unique constraint uq_t_customer_root_prefix"),
        )
        with pytest.raises(BizError) as exc_info:
            await service.create_customer(data)
        assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc_info.value.http_status == http_status.HTTP_409_CONFLICT
        assert "F" in exc_info.value.message


class TestUpdateCustomerSerialPrefix:
    """serial_prefix 在 update_customer 上的行为。

    - 一级客户：payload 给非 None 才更新；不改时维持原值。
    - 叶子客户：忽略 payload 里的 serial_prefix。
    - 冲突：IntegrityError → 409。
    """

    def _patch_get_by_id(
        self,
        mock_repo: CustomerRepository,
        cust: TCustomer,
        parent: TCustomer | None = None,
    ) -> None:
        """把 _make_customer 出来的实例 attach 到 session（绕过 lazy load）。"""
        # 给 cust 注入一个空 sa 实例状态，让 get_by_id 直接返回它
        mock_repo.get_by_id.return_value = cust
        # list_by_ids 用于构造 CustomerOut（拿 parent_name）—— 我们让 parent 注入
        if parent is not None:
            mock_repo.list_by_ids.return_value = [parent]

    async def test_root_updates_prefix(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """一级客户改 prefix F → Z 成功。"""
        cust = _make_customer(
            id=1, name="Root", parent_id=None, serial_prefix="F",
        )
        self._patch_get_by_id(mock_repo, cust)

        data = CustomerUpdateRequest(
            name=None, parent_id=None, serial_prefix="Z",
        )
        await service.update_customer("1", data)

        updated: TCustomer = mock_repo.update.call_args[0][0]
        assert updated.serial_prefix == "Z"
        assert updated.name == "Root"  # 没改 name 保持原值

    async def test_root_no_prefix_noop(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """一级客户 update 不带 prefix → 不动原值。"""
        cust = _make_customer(
            id=1, name="Root", parent_id=None, serial_prefix="F",
        )
        self._patch_get_by_id(mock_repo, cust)

        data = CustomerUpdateRequest(name="Renamed", parent_id=None)
        await service.update_customer("1", data)

        updated: TCustomer = mock_repo.update.call_args[0][0]
        assert updated.serial_prefix == "F"  # 未变
        assert updated.name == "Renamed"

    async def test_leaf_ignores_prefix(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """叶子客户传 prefix 被忽略。"""
        leaf = _make_customer(
            id=10, name="Leaf", parent_id=1, serial_prefix=None,
        )
        parent = _make_customer(id=1, name="Root", parent_id=None, serial_prefix="F")
        # update_customer → get_customer (via update); get_customer 调 get_by_id +
        # list_by_ids. 第一次 get_by_id = leaf; list_by_ids = [parent]
        mock_repo.get_by_id.side_effect = [leaf, parent]
        mock_repo.list_by_ids.return_value = [parent]

        data = CustomerUpdateRequest(name=None, parent_id=None, serial_prefix="Q")
        await service.update_customer("10", data)

        updated: TCustomer = mock_repo.update.call_args[0][0]
        # 叶子客户 serial_prefix 字段不应被 payload 改动（保持 None）
        assert updated.serial_prefix is None

    async def test_root_duplicate_prefix_on_update_409(
        self,
        service: CustomerService,
        mock_repo: CustomerRepository,
    ) -> None:
        """一级客户 update 时撞 prefix 冲突 → DB IntegrityError → 409。"""
        cust = _make_customer(
            id=1, name="Root", parent_id=None, serial_prefix="F",
        )
        self._patch_get_by_id(mock_repo, cust)

        mock_repo.update.side_effect = IntegrityError(
            "UPDATE", {}, Exception("unique constraint uq_t_customer_root_prefix"),
        )
        data = CustomerUpdateRequest(name=None, parent_id=None, serial_prefix="L")
        with pytest.raises(BizError) as exc_info:
            await service.update_customer("1", data)
        assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc_info.value.http_status == http_status.HTTP_409_CONFLICT
        assert "L" in exc_info.value.message
