"""工序 (Process) service 单元测试。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model import TProcess
from model.enums import ProcessCategory
from schema.process import ProcessCreateRequest, ProcessUpdateRequest
from service.process import ProcessService


def _now() -> datetime:
    return datetime(2026, 1, 1)


def _p(id: int = 1, code: str = "车") -> TProcess:
    p = TProcess(
        id=id, code=code, name="车床加工", category="INHOUSE",
        sort_order=0,
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


# =============================================================================
# update_process — MissingGreenlet 回归保护
# =============================================================================
# 2026-07-14 修复：service/process.py::update_process 在 flush 之后必须显式
# `await self.processes.session.refresh(p)`，否则 _process_to_out 同步读取
# `p.updated_at` 会在 async session 里抛 MissingGreenlet（AuditMixin 的
# onupdate=func.now() 会让该列在 UPDATE flush 后被 expire）。
# 本测试用 AsyncMock 验证 fix 不会被回退掉。真实 expire 行为需要集成测试
# （tests/integration/ 暂未建）。
# =============================================================================


@pytest.mark.asyncio
async def test_update_process_refreshes_after_update() -> None:
    """update_process 必须调用 session.refresh 来回填 expired updated_at。"""
    repo = AsyncMock()
    existing = _p(id=42, code="车")
    repo.get_by_id = AsyncMock(return_value=existing)
    repo.update = AsyncMock(return_value=existing)
    # repo.session.refresh 由 AsyncMock 默认提供，挂在 repo.session.refresh 上

    svc = ProcessService(processes=repo)
    await svc.update_process(
        42, ProcessUpdateRequest(name="车床加工v2"),
    )

    repo.update.assert_awaited_once_with(existing)
    repo.session.refresh.assert_awaited_once_with(existing)


@pytest.mark.asyncio
async def test_update_process_returns_full_out() -> None:
    """update_process 返回完整 ProcessOut（含 updated_at）。"""
    repo = AsyncMock()
    existing = _p(id=7, code="铣")
    repo.get_by_id = AsyncMock(return_value=existing)
    repo.update = AsyncMock(return_value=existing)

    svc = ProcessService(processes=repo)
    out = await svc.update_process(
        7,
        ProcessUpdateRequest(
            name="铣床加工v2", sort_order=10, description="升级",
        ),
    )

    assert out.id == 7
    assert out.code == "铣"
    assert out.name == "铣床加工v2"
    assert out.sort_order == 10
    assert out.description == "升级"
    assert out.updated_at == _now()


@pytest.mark.asyncio
async def test_update_process_not_found_raises() -> None:
    """update_process 在 process 不存在时抛 BIZ_PROCESS_NOT_FOUND。"""
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)

    svc = ProcessService(processes=repo)
    with pytest.raises(BizError) as exc_info:
        await svc.update_process(
            999, ProcessUpdateRequest(name="不存在的工序"),
        )

    assert exc_info.value.code == ErrCode.BIZ_PROCESS_NOT_FOUND
    # 关键：未找到时不应触发 update / refresh
    repo.update.assert_not_awaited()
    repo.session.refresh.assert_not_awaited()