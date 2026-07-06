"""工序 (Process) service 单元测试。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TProcess
from model.enums import ProcessCategory
from schema.process import ProcessCreateRequest
from service.process import ProcessService


def _now() -> datetime:
    return datetime(2026, 1, 1)


def _p(id: int = 1, code: str = "车") -> TProcess:
    p = TProcess(
        id=id, code=code, name="车床加工", category="INHOUSE",
        is_inspection=False, sort_order=0,
    )
    p.created_at = _now()
    p.updated_at = _now()
    p.description = None
    p.deleted_at = None
    return p


def _mock_create(obj):
    if getattr(obj, "created_at", None) is None:
        obj.created_at = _now()
    if getattr(obj, "updated_at", None) is None:
        obj.updated_at = _now()
    return obj


@pytest.mark.asyncio
async def test_create_process_success():
    repo = AsyncMock()
    repo.get_by_code = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=_mock_create)

    svc = ProcessService(processes=repo)
    out = await svc.create_process(ProcessCreateRequest(
        code="车", name="车床加工", category=ProcessCategory.INHOUSE,
    ))

    assert out.code == "车"
    assert out.category == ProcessCategory.INHOUSE
    repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_process_duplicate_conflict():
    repo = AsyncMock()
    repo.get_by_code = AsyncMock(return_value=_p())

    svc = ProcessService(processes=repo)
    with pytest.raises(BizError) as exc_info:
        await svc.create_process(ProcessCreateRequest(
            code="车", name="车床加工", category=ProcessCategory.INHOUSE,
        ))

    assert exc_info.value.code == ErrCode.BIZ_PROCESS_DUPLICATE_CODE


@pytest.mark.asyncio
async def test_soft_delete_in_use_raises():
    repo = AsyncMock()
    junction = AsyncMock()
    junction.list_by_process = AsyncMock(return_value=[object()])

    svc = ProcessService(processes=repo, junction_repo=junction)

    with pytest.raises(BizError) as exc_info:
        await svc.soft_delete_process(1)

    assert exc_info.value.code == ErrCode.BIZ_PROCESS_IN_USE