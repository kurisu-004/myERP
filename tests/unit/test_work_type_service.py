"""工种 (WorkType) service 单元测试。AsyncMock 覆盖 repository。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TWorkType
from schema.work_type import WorkTypeCreateRequest, WorkTypeUpdateRequest
from service.work_type import WorkTypeService


def _now() -> datetime:
    return datetime(2026, 1, 1)


def _wt(id: int = 1, code: str = "车床", name: str = "车床工") -> TWorkType:
    wt = TWorkType(id=id, code=code, name=name, sort_order=0)
    wt.created_at = _now()
    wt.updated_at = _now()
    wt.description = None
    wt.deleted_at = None
    return wt


def _mock_create(obj):
    """Mock repo.create：模拟 DB 写入时给 audit 字段填默认值。"""
    if getattr(obj, "created_at", None) is None:
        obj.created_at = _now()
    if getattr(obj, "updated_at", None) is None:
        obj.updated_at = _now()
    return obj


@pytest.mark.asyncio
async def test_create_work_type_success():
    repo = AsyncMock()
    repo.get_by_code = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=_mock_create)

    svc = WorkTypeService(work_types=repo)
    out = await svc.create_work_type(WorkTypeCreateRequest(code="车床", name="车床工"))

    assert out.code == "车床"
    assert out.name == "车床工"
    repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_work_type_duplicate_conflict():
    repo = AsyncMock()
    repo.get_by_code = AsyncMock(return_value=_wt())

    svc = WorkTypeService(work_types=repo)
    with pytest.raises(BizError) as exc_info:
        await svc.create_work_type(WorkTypeCreateRequest(code="车床", name="x"))

    assert exc_info.value.code == ErrCode.BIZ_WORK_TYPE_DUPLICATE_CODE


@pytest.mark.asyncio
async def test_get_work_type_not_found():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)

    svc = WorkTypeService(work_types=repo)
    with pytest.raises(BizError) as exc_info:
        await svc.get_work_type(999)

    assert exc_info.value.code == ErrCode.BIZ_WORK_TYPE_NOT_FOUND


@pytest.mark.asyncio
async def test_update_work_type_changes_name():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_wt())
    repo.update = AsyncMock(side_effect=lambda wt: wt)

    svc = WorkTypeService(work_types=repo)
    out = await svc.update_work_type(1, WorkTypeUpdateRequest(name="新车床工"))

    assert out.name == "新车床工"


@pytest.mark.asyncio
async def test_soft_delete_in_use_raises():
    """junction_repo 有未删映射时拒绝软删。"""
    repo = AsyncMock()
    junction = AsyncMock()
    junction.list_by_work_type = AsyncMock(return_value=[object()])

    svc = WorkTypeService(work_types=repo, junction_repo=junction)

    with pytest.raises(BizError) as exc_info:
        await svc.soft_delete_work_type(1)

    assert exc_info.value.code == ErrCode.BIZ_WORK_TYPE_IN_USE