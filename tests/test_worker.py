"""工人 service 集成测试（走真实 PostgreSQL）。

覆盖：
- 修复 1（MissingGreenlet #1）：
  * test_deactivate_returns_full_out —— 直接覆盖产线报的 500
  * test_reactivate_round_trip —— 同源 bug，覆盖
- 修复 3（业务漏洞）：test_deactivate_rejects_when_holding_parts
- 其余：404 路径、already-inactive 路径

_worker_test/_make_worker 风格参考 tests/test_password_change.py。
"""
from __future__ import annotations

from datetime import date

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TCustomer, TPart, TWorker
from model.enums import PartLocation, PartStatus
from repository.part import PartRepository
from repository.work_type import WorkTypeRepository
from repository.worker import WorkerRepository
from schema.worker import WorkerListQuery
from service.worker import WorkerService
from utils.id_gen import new_id

pytestmark = pytest.mark.asyncio


# ============================================================
# helpers
# ============================================================


def _build_worker_service(session, *, actor_id: int | None = None) -> WorkerService:
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
    return WorkerService(
        workers=WorkerRepository(session),
        work_types=WorkTypeRepository(session),
        parts=PartRepository(session),
        current_user=current,
    )


async def _make_worker(
    session, *, badge: str = "W-001", name: str = "测试员"
) -> TWorker:
    w = TWorker(id=new_id(), badge_code=badge, name=name, is_active=True)
    session.add(w)
    await session.flush()
    return w


async def _seed_part_held_by_worker(
    session, worker_id: int, *, status: str = PartStatus.IN_PROCESS.value
) -> TPart:
    """塞一个由指定 worker 持有的 active part（location=WORKER）。"""
    customer = TCustomer(name="工人持件测试客户")
    session.add(customer)
    await session.flush()

    part = TPart(
        id=new_id(),
        name="工人持件测试零件",
        drawing_no=f"DWG-{customer.id}",
        applicant_name="tester",
        quantity=1,
        request_date=date(2026, 8, 1),
        planned_delivery_date=date(2026, 8, 30),
        customer_id=customer.id,
        status=status,
        location=PartLocation.WORKER.value,
        current_holder_id=worker_id,
    )
    session.add(part)
    await session.flush()
    return part


# ============================================================
# 修复 1：MissingGreenlet 回归测试
# ============================================================


async def test_deactivate_returns_full_out_no_missing_greenlet(clean_db):
    """直接覆盖产线报的 500：deactivate 后 _to_out 同步读 updated_at 必须成功。

    修复前：flush 后访问 w.updated_at 触发 MissingGreenlet → 500。
    修复后：session.refresh(w) 显式回填 → 返回完整 WorkerOut。
    """
    session = clean_db
    w = await _make_worker(session, badge="W-DE-001", name="待停用")

    svc = _build_worker_service(session, actor_id=1)
    out = await svc.deactivate(w.id)

    # _to_out 读 updated_at 没爆（这是修复目标）
    assert out.id == w.id
    assert out.is_active is False
    assert out.updated_at is not None  # ← 关键：非 None 且不抛 MissingGreenlet
    # OCC 计数 +1
    assert out.version >= 1
    # 新行为：deactivate 不再写 deleted_at，只动 is_active
    await session.refresh(w)
    assert w.deleted_at is None


async def test_reactivate_round_trip_no_missing_greenlet(clean_db):
    """reactivate 同样的同源 bug：flush 后读 updated_at 触发 MissingGreenlet。
    修复后 round-trip 正常返回。
    """
    session = clean_db
    w = await _make_worker(session, badge="W-RE-001", name="再启用")

    svc = _build_worker_service(session, actor_id=1)
    out = await svc.deactivate(w.id)
    assert out.is_active is False
    await session.refresh(w)
    assert w.deleted_at is None

    out = await svc.reactivate(w.id)
    assert out.is_active is True
    assert out.updated_at is not None  # 关键：非 None
    await session.refresh(w)
    assert w.deleted_at is None


# ============================================================
# 修复 3：BIZ_WORKER_IN_USE 业务校验
# ============================================================


async def test_deactivate_rejects_when_holding_active_part(clean_db):
    """工人持有 IN_PROCESS 零件时停用应被 BIZ_WORKER_IN_USE 拒绝。

    注意：_assert_not_holding_parts 自己新开 SessionLocal() 查 t_part，
    所以 fixture 里 seed 的 part 必须先 commit（否则新 session 看不到）。
    """
    session = clean_db
    w = await _make_worker(session, badge="W-IN-001", name="持件中")
    await _seed_part_held_by_worker(session, w.id, status=PartStatus.IN_PROCESS.value)
    await session.commit()  # ← 必需：新 session 才能读到

    svc = _build_worker_service(session, actor_id=1)
    with pytest.raises(BizError) as exc_info:
        await svc.deactivate(w.id)
    assert exc_info.value.code == ErrCode.BIZ_WORKER_IN_USE
    assert exc_info.value.http_status == 409

    # 工人状态未被改动（仍在职）
    await session.refresh(w)
    assert w.is_active is True
    assert w.deleted_at is None


async def test_deactivate_allows_when_only_completed_parts_exist(clean_db):
    """工人历史只有 COMPLETED 零件时，停用应被放行。

    边界：BIZ_WORKER_IN_USE 只挡 IN_PROCESS / INSPECTION / REPAIRING；
    终态 / 已交付 / 已取消不挡。
    """
    session = clean_db
    w = await _make_worker(session, badge="W-DONE-001", name="已完成")
    await _seed_part_held_by_worker(
        session, w.id, status=PartStatus.COMPLETED.value
    )
    await session.commit()

    svc = _build_worker_service(session, actor_id=1)
    out = await svc.deactivate(w.id)  # 不抛
    assert out.is_active is False
    await session.refresh(w)
    assert w.deleted_at is None  # 新行为：deactivate 不写 deleted_at


# ============================================================
# 既有 404 / 业务路径（防止 _assert_not_holding_parts 改变既有行为）
# ============================================================


async def test_deactivate_not_found_404(clean_db):
    session = clean_db
    svc = _build_worker_service(session, actor_id=1)
    with pytest.raises(BizError) as exc_info:
        await svc.deactivate(999999999)
    assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
    assert exc_info.value.http_status == 404


async def test_deactivate_is_idempotent(clean_db):
    """2026-08-04 起：deactivate 不再写 deleted_at，二次停用从 404 变为 200 幂等返回。
    无实际变更时不触发 UPDATE，version 保持不变。
    """
    session = clean_db
    w = await _make_worker(session, badge="W-IDEMP-001", name="幂等停用")
    svc = _build_worker_service(session, actor_id=1)
    out1 = await svc.deactivate(w.id)
    assert out1.is_active is False
    out2 = await svc.deactivate(w.id)  # 不抛
    assert out2.id == w.id
    assert out2.is_active is False
    assert out2.version == out1.version  # 无 dirty 字段 → 跳过 UPDATE → version 不变


async def test_deactivated_worker_visible_in_list_with_is_active_false(clean_db):
    """回归用户报的 bug：deactivate 后 ?is_active=false 必须能查到该行。

    旧行为：deactivate 写 deleted_at → 被 list_with_filters 的软删守卫隐藏 →
    前端「状态：停用」筛选永远空。新行为：deleted_at 保持 NULL，行可见。
    """
    session = clean_db
    w = await _make_worker(session, badge="W-VISIBLE-001", name="应可见")
    svc = _build_worker_service(session, actor_id=1)
    await svc.deactivate(w.id)  # 不抛

    out = await svc.list_workers(WorkerListQuery(is_active=False, limit=200, offset=0))
    ids = [str(o.id) for o in out.items]
    assert str(w.id) in ids
    assert out.total >= 1
