"""乐观锁 (OCC) 单元测试。

通过 mock repository 的 update() / soft_delete() 抛
`sqlalchemy.orm.exc.StaleDataError`，验证全局异常处理器把
SQLAlchemy 异常正确转 BizError(BIZ_VERSION_CONFLICT, 409)。

不依赖真实 PostgreSQL（单元测试走 mocked repo）。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm.exc import StaleDataError

from core.error_code import ErrCode
from core.exception import BizError
from main import app
from model import TPart
from repository import (
    ApplicantRepository,
    CustomerRepository,
    PartEventRepository,
    PartFileRepository,
    PartRepository,
    ProcessRepository,
    SerialCounterRepository,
    ShelfRepository,
    WorkerRepository,
)
from schema.part import PartUpdateRequest
from service.part import PartService

pytestmark = pytest.mark.asyncio


# ============================================================
# StaleDataError → BizError 转换（直接测全局 handler）
# ============================================================


class TestStaleDataErrorHandler:
    """直接测全局 StaleDataError 异常处理器的转换。"""

    async def test_handler_returns_409_envelope(self) -> None:
        """调 FastAPI 注册的 StaleDataError handler，验证返回 409 + R.fail 信封。"""
        from core.exception_handler import register_exception_handlers
        from core.response import R

        # 注册一个最小 FastAPI app + handler
        from fastapi import FastAPI

        test_app = FastAPI()
        register_exception_handlers(test_app)

        @test_app.get("/raise")
        async def _raise() -> None:
            raise StaleDataError(
                "UPDATE t_part SET version=:v1 WHERE id=:id AND version=:v0",
                {"id": 1, "v0": 0, "v1": 1},
                Exception("0 rows affected"),
            )

        client = TestClient(test_app)
        resp = client.get("/raise")

        assert resp.status_code == 409
        body = resp.json()
        # 校验统一响应信封 + 错误码 + 文案
        assert body["code"] == int(ErrCode.BIZ_VERSION_CONFLICT)
        assert "已被其他用户修改" in body["message"]
        assert body["data"] is None


# ============================================================
# Service 层 mock 测试：update 抛 StaleDataError 的传递
# ============================================================


def _make_part(id: int = 1, customer_id: int = 10, **kwargs) -> TPart:
    return TPart(
        id=id,
        serial_no=f"L{id:04d}",
        name="Test Part",
        drawing_no="DWG-001",
        applicant_name="tester",
        quantity=1,
        request_date=datetime(2026, 7, 1).date(),
        planned_delivery_date=datetime(2026, 7, 30).date(),
        customer_id=customer_id,
        **kwargs,
    )


def _make_customer(id: int = 10, name: str = "ChildCorp") -> dict:
    """最小化 customer dict（service 只用 customer_id 索引）."""
    return {
        "id": id,
        "name": name,
        "parent_id": None,
        "version": 0,
    }


class TestServicePropagatesStaleDataError:
    """验证 service.update_part 把 repository 抛的 StaleDataError
    一路传到 FastAPI 层（中间不吞）。
    """

    async def test_update_part_stale_propagates(self) -> None:
        part = _make_part()
        mock_parts = AsyncMock(spec=PartRepository)
        mock_customers = AsyncMock(spec=CustomerRepository)
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_parts.update = AsyncMock(
            side_effect=StaleDataError(
                "UPDATE t_part SET version=:v1 WHERE id=:id AND version=:v0",
                {"id": 1, "v0": 0, "v1": 1},
                Exception("0 rows affected"),
            ),
        )
        mock_customers.list_by_ids = AsyncMock(
            return_value=[_make_customer()],
        )
        # 其余 dep 都给 AsyncMock 占位
        mock_workers = AsyncMock(spec=WorkerRepository)
        mock_events = AsyncMock(spec=PartEventRepository)
        mock_serial = AsyncMock(spec=SerialCounterRepository)
        mock_shelves = AsyncMock(spec=ShelfRepository)
        mock_processes = AsyncMock(spec=ProcessRepository)
        svc = PartService(
            parts=mock_parts,
            customers=mock_customers,
            workers=mock_workers,
            events=mock_events,
            serial_counters=mock_serial,
            shelves=mock_shelves,
            processes=mock_processes,
        )

        with pytest.raises(StaleDataError):
            await svc.update_part(
                part_id=1,
                data=PartUpdateRequest(name="Updated"),
            )

    async def test_soft_delete_stale_propagates(self) -> None:
        """验证 soft_delete 也走同样的 OCC 路径。"""
        part = _make_part()
        mock_parts = AsyncMock(spec=PartRepository)
        mock_parts.get_by_id = AsyncMock(return_value=part)
        mock_parts.soft_delete = AsyncMock(
            side_effect=StaleDataError(
                "UPDATE t_part SET deleted_at=:v WHERE id=:id AND version=:v0",
                {"id": 1, "v0": 0, "v": "2026-07-15"},
                Exception("0 rows affected"),
            ),
        )
        mock_customers = AsyncMock(spec=CustomerRepository)
        mock_workers = AsyncMock(spec=WorkerRepository)
        mock_events = AsyncMock(spec=PartEventRepository)
        mock_serial = AsyncMock(spec=SerialCounterRepository)
        mock_shelves = AsyncMock(spec=ShelfRepository)
        svc = PartService(
            parts=mock_parts,
            customers=mock_customers,
            workers=mock_workers,
            events=mock_events,
            serial_counters=mock_serial,
            shelves=mock_shelves,
        )

        with pytest.raises(StaleDataError):
            await svc.soft_delete_part(part_id=1)
