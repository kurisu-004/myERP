"""工种↔工序映射 service 单元测试。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TProcess, TWorkType, TWorkTypeProcess
from schema.work_type_process import SetWorkTypeProcessRequest
from service.work_type_process import WorkTypeProcessService


def _now() -> datetime:
    return datetime(2026, 1, 1)


def _wt(id: int = 1) -> TWorkType:
    wt = TWorkType(id=id, code="车床", name="车床工", sort_order=0)
    wt.created_at = _now()
    wt.updated_at = _now()
    wt.description = None
    wt.deleted_at = None
    return wt


def _p(id: int, code: str) -> TProcess:
    p = TProcess(
        id=id, code=code, name=code, category="INHOUSE",
        sort_order=0,
    )
    p.created_at = _now()
    p.updated_at = _now()
    p.description = None
    p.deleted_at = None
    return p


def _wp(wt_id: int, p_id: int, sort: int = 0) -> TWorkTypeProcess:
    wp = TWorkTypeProcess(
        id=1000 + p_id, work_type_id=wt_id, process_id=p_id, sort_order=sort,
    )
    wp.created_at = _now()
    wp.updated_at = _now()
    wp.deleted_at = None
    return wp


def _mock_create(obj):
    if getattr(obj, "created_at", None) is None:
        obj.created_at = _now()
    if getattr(obj, "updated_at", None) is None:
        obj.updated_at = _now()
    return obj


@pytest.mark.asyncio
async def test_set_for_work_type_replaces_existing():
    """整体替换：清掉旧的，插入新的。"""
    wt_repo = AsyncMock()
    wt_repo.get_by_id = AsyncMock(return_value=_wt(1))

    p_repo = AsyncMock()
    p_repo.list_by_ids = AsyncMock(
        side_effect=lambda ids, **kw: [_p(i, f"P{i}") for i in ids]
    )

    junction = AsyncMock()
    junction.delete_by_work_type = AsyncMock()
    junction.create = AsyncMock(side_effect=lambda row: row)
    junction.list_by_work_type = AsyncMock(return_value=[])

    svc = WorkTypeProcessService(work_types=wt_repo, processes=p_repo, junction=junction)
    out = await svc.set_for_work_type(
        1, SetWorkTypeProcessRequest(process_ids=[10, 20]),
    )

    junction.delete_by_work_type.assert_awaited_once_with(1)
    assert junction.create.await_count == 2
    assert out.id == 1  # 主键 id
    assert len(out.processes) == 0  # list_by_work_type 返回空 (mock)


@pytest.mark.asyncio
async def test_set_with_invalid_process_id_raises():
    wt_repo = AsyncMock()
    wt_repo.get_by_id = AsyncMock(return_value=_wt(1))

    p_repo = AsyncMock()
    p_repo.list_by_ids = AsyncMock(return_value=[])  # 都找不到

    junction = AsyncMock()

    svc = WorkTypeProcessService(work_types=wt_repo, processes=p_repo, junction=junction)

    with pytest.raises(BizError) as exc_info:
        await svc.set_for_work_type(
            1, SetWorkTypeProcessRequest(process_ids=[999]),
        )

    assert exc_info.value.code == ErrCode.BIZ_PROCESS_NOT_FOUND


@pytest.mark.asyncio
async def test_set_for_work_type_dedupes_process_ids():
    """重复 process_id 应去重，保序。"""
    wt_repo = AsyncMock()
    wt_repo.get_by_id = AsyncMock(return_value=_wt(1))

    p_repo = AsyncMock()
    p_repo.list_by_ids = AsyncMock(
        side_effect=lambda ids, **kw: [_p(i, f"P{i}") for i in ids]
    )

    junction = AsyncMock()
    junction.delete_by_work_type = AsyncMock()
    junction.create = AsyncMock(side_effect=lambda row: row)
    junction.list_by_work_type = AsyncMock(return_value=[])

    svc = WorkTypeProcessService(work_types=wt_repo, processes=p_repo, junction=junction)
    await svc.set_for_work_type(
        1, SetWorkTypeProcessRequest(process_ids=[10, 20, 10, 30, 20]),
    )

    # 应只插 3 条 (10, 20, 30)
    assert junction.create.await_count == 3


@pytest.mark.asyncio
async def test_work_type_not_found_raises():
    wt_repo = AsyncMock()
    wt_repo.get_by_id = AsyncMock(return_value=None)

    p_repo = AsyncMock()
    junction = AsyncMock()

    svc = WorkTypeProcessService(work_types=wt_repo, processes=p_repo, junction=junction)

    with pytest.raises(BizError) as exc_info:
        await svc.set_for_work_type(
            999, SetWorkTypeProcessRequest(process_ids=[1]),
        )

    assert exc_info.value.code == ErrCode.BIZ_WORK_TYPE_NOT_FOUND