"""工种 service 集成测试（走真实 PostgreSQL）。

仅放需要真实 DB 才能验证的回归（`tests/unit/test_work_type_service.py` 覆盖纯 mock 用例）。

当前覆盖：
- 修复 2（MissingGreenlet #2）：test_update_work_type_returns_full_out
  —— 2026-08-04 修复前：flush 后 _work_type_to_out 同步读 updated_at
  触发 MissingGreenlet → 500。修复后：session.refresh(wt) 显式回填。
"""
from __future__ import annotations

import pytest

from core.permission import CurrentUser
from model import TWorkType
from repository.work_type import WorkTypeRepository
from service.work_type import WorkTypeService
from schema.work_type import WorkTypeUpdateRequest
from utils.id_gen import new_id

pytestmark = pytest.mark.asyncio


def _build_service(session, *, actor_id: int | None = None) -> WorkTypeService:
    current = None
    if actor_id is not None:
        current = CurrentUser(
            id=actor_id,
            username="actor",
            full_name="Actor",
            is_active=True,
            roles=("MANAGER",),
            shelf_ids=(),
        )
    return WorkTypeService(work_types=WorkTypeRepository(session), current_user=current)


async def test_update_work_type_returns_full_out_no_missing_greenlet(clean_db):
    """直接覆盖 work_type 的 MissingGreenlet 同源 bug：update 后 _to_out 必须成功。"""
    session = clean_db
    wt = TWorkType(id=new_id(), code="WT-001", name="车床", sort_order=0)
    session.add(wt)
    await session.flush()

    svc = _build_service(session, actor_id=1)
    out = await svc.update_work_type(wt.id, WorkTypeUpdateRequest(name="车床工"))

    # _to_out 读 updated_at 没爆（这是修复目标）
    assert out.id == wt.id
    assert out.name == "车床工"
    assert out.updated_at is not None
    assert out.version >= 1
