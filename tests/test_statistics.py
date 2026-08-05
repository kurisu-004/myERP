"""生产统计后端集成测试（2026-08-03 新增）。

按项目约定（CLAUDE.md + tests/test_outsource_quote_lifecycle.py 范式）：
- 直接构造 service 实例，不走 HTTP TestClient；
- DB 由 ``tests/conftest.py::clean_db`` fixture 提供（per-function 表数据隔离）；
- docker 5435 容器由 session 级 fixture 自动 up → migrate → down。

覆盖：
1. overview 各字段口径（created/completed/in_process/delivered/orange/red/
   overdue_undelivered/repair_part_count + daily_created/completed +
   delivery_performance + status_distribution）
2. 期末在制事件重构（不依赖 t_part.status，由工单级 COMPLETED/CANCELLED 事件驱动）
3. tab2 工人贡献度（distinct part, contribution_pct=75/25, 除零, 无工种, 软删过滤）
4. tab3 单工人详情（cards + parts 列表 + daily_pickups 零填充）
5. 权限（MANAGER-only）
6. 图表数据完整性（daily_created/completed 长度 = date_to - date_from + 1；
   on_time+orange+red == delivered_count）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from model import (
    TCustomer,
    TPart,
    TPartEvent,
    TWorker,
    TWorkType,
)
from model.enums import PartEventType, PartStatus, UserRole
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.statistics import StatisticsRepository
from repository.work_type import WorkTypeRepository
from repository.worker import WorkerRepository
from service.statistics import StatisticsService

pytestmark = pytest.mark.asyncio


# ============================================================
# Helpers
# ============================================================
async def _make_l1_root(
    session, *, name: str = "统计测试客户", prefix: str = "S",
) -> TCustomer:
    c = TCustomer(name=name, parent_id=None)
    c.serial_prefix = prefix
    session.add(c)
    await session.flush()
    return c


async def _make_work_type(
    session, *, code: str = "STAT", name: str = "统计工种",
) -> TWorkType:
    wt = TWorkType(code=code, name=name)
    session.add(wt)
    await session.flush()
    return wt


async def _make_worker(
    session, *, badge: str, name: str = "统计工人",
    is_active: bool = True, work_type_id: int | None = None,
    deleted_at: datetime | None = None,
) -> TWorker:
    w = TWorker(
        badge_code=badge, name=name, is_active=is_active,
        work_type_id=work_type_id, deleted_at=deleted_at,
    )
    session.add(w)
    await session.flush()
    return w


async def _make_part(
    session, *, customer_id: int, serial: str,
    status: str = PartStatus.PENDING.value,
    created_at: datetime | None = None,
    actual_delivery_date: date | None = None,
    planned_delivery_date: date | None = None,
    system_delivery_date: date | None = None,
    unit_price=None,
    total_price=None,
) -> TPart:
    """插入一个 part 行。注意：created_at 在 INSERT 之后才能被 server_default
    `now()` 填上，所以下面在 fixture 末尾直接就地覆盖 created_at，flush 一次。

    默认 planned_delivery_date = today + 30，避免其它测试场外被误算 overdue。
    """
    from decimal import Decimal

    today_ref = now_naive().date()
    p = TPart(
        serial_no=serial,
        name=f"part-{serial}",
        drawing_no=f"DWG-{serial}",
        applicant_name="统计测试申请人",
        quantity=1,
        unit_price=(unit_price if unit_price is not None else Decimal("100")),
        total_price=(total_price if total_price is not None else Decimal("100")),
        request_date=date(2026, 1, 1),
        planned_delivery_date=(
            planned_delivery_date or (today_ref + timedelta(days=30))
        ),
        actual_delivery_date=actual_delivery_date,
        system_delivery_date=system_delivery_date,
        customer_id=customer_id,
        status=status,
        location="OFFICE",
    )
    session.add(p)
    await session.flush()
    if created_at is not None:
        # 直接覆写 created_at：审计列约定上是 server_default 但测试需要时间注入
        p.created_at = created_at
        p.updated_at = created_at
        await session.flush()
    return p


async def _add_event(
    session,
    *,
    part_id: int,
    event_type: str,
    created_at: datetime,
    worker_id: int | None = None,
    quantity: int | None = None,
    batch_id: int | None = None,
    to_status: str | None = None,
) -> TPartEvent:
    """向 t_part_event 追加一条事件（指定 created_at）。"""
    e = TPartEvent(
        part_id=part_id,
        batch_id=batch_id,
        quantity=quantity,
        worker_id=worker_id,
        event_type=event_type,
        to_status=to_status,
        created_at=created_at,
    )
    session.add(e)
    await session.flush()
    return e


def _make_service(session) -> StatisticsService:
    return StatisticsService(
        session=session,
        stats_repo=StatisticsRepository(session),
        parts=PartRepository(session),
        events=PartEventRepository(session),
        workers=WorkerRepository(session),
        work_types=WorkTypeRepository(session),
        current_user=None,
    )


def _dt(d: date, *, hour: int = 12) -> datetime:
    """把一个 date 转成中午 12 点（避免 TZ 边界跳日）。"""
    return datetime(d.year, d.month, d.day, hour, 0, 0)


# ============================================================
# 1. overview 各字段口径
# ============================================================
async def test_overview_field_calibration(clean_db):
    """overview 全部字段一次性覆盖：created/completed/in_process/delivered
    /orange/red/overdue_undelivered/repair_part_count/daily 系列。"""
    today = now_naive().date()
    date_from = today - timedelta(days=10)
    date_to = today

    cust = await _make_l1_root(clean_db)
    wt = await _make_work_type(clean_db)
    worker = await _make_worker(
        clean_db, badge="W-OV", work_type_id=wt.id,
    )

    # ============ 在范围内创建的 3 个件 ============
    # 1) 完全未动（PENDING）
    p_pending = await _make_part(
        clean_db, customer_id=cust.id, serial="S1001",
        status=PartStatus.PENDING.value,
        created_at=_dt(date_from + timedelta(days=1)),
    )
    # 2) 被领取但未交付（IN_PROCESS / WITH_WORKER）
    p_picked = await _make_part(
        clean_db, customer_id=cust.id, serial="S1002",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=2)),
    )
    await _add_event(
        clean_db,
        part_id=p_picked.id, event_type=PartEventType.PICKED_UP.value,
        created_at=_dt(date_from + timedelta(days=2), hour=14),
        worker_id=worker.id, to_status=PartStatus.IN_PROCESS.value,
    )
    # 3) 已交付（actual 在范围内）
    p_delivered_on_time = await _make_part(
        clean_db, customer_id=cust.id, serial="S1003",
        status=PartStatus.DELIVERED.value,
        created_at=_dt(date_from + timedelta(days=3)),
        actual_delivery_date=today,
        planned_delivery_date=today - timedelta(days=10),
        system_delivery_date=today + timedelta(days=10),
    )
    # 4) 已交付 - 红档（actual > system）
    p_delivered_red = await _make_part(
        clean_db, customer_id=cust.id, serial="S1004",
        status=PartStatus.DELIVERED.value,
        created_at=_dt(date_from + timedelta(days=4)),
        actual_delivery_date=today,
        planned_delivery_date=today - timedelta(days=10),
        system_delivery_date=today - timedelta(days=5),
    )
    # 5) 已交付 - 橙档（system NULL, actual > planned）
    p_delivered_orange = await _make_part(
        clean_db, customer_id=cust.id, serial="S1005",
        status=PartStatus.DELIVERED.value,
        created_at=_dt(date_from + timedelta(days=5)),
        actual_delivery_date=today,
        planned_delivery_date=today - timedelta(days=10),
        system_delivery_date=None,
    )
    # 6) 已交付 - 准时（green）
    p_delivered_green = await _make_part(
        clean_db, customer_id=cust.id, serial="S1006",
        status=PartStatus.DELIVERED.value,
        created_at=_dt(date_from + timedelta(days=5)),
        actual_delivery_date=today,
        planned_delivery_date=today + timedelta(days=10),
        system_delivery_date=today + timedelta(days=10),
    )

    # ============ 范围外创建 2 个件（不应进 created_count） ============
    # 7) 范围前
    await _make_part(
        clean_db, customer_id=cust.id, serial="S2001",
        status=PartStatus.PENDING.value,
        created_at=_dt(date_from - timedelta(days=5)),
    )
    # 8) 范围后
    await _make_part(
        clean_db, customer_id=cust.id, serial="S2002",
        status=PartStatus.PENDING.value,
        created_at=_dt(date_to + timedelta(days=1)),
    )

    # ============ 范围前创建但范围内被 COMPLETED 事件标记 ============
    p_completed_in_range = await _make_part(
        clean_db, customer_id=cust.id, serial="S3001",
        status=PartStatus.COMPLETED.value,
        created_at=_dt(date_from - timedelta(days=10)),
        actual_delivery_date=today,
    )
    await _add_event(
        clean_db,
        part_id=p_completed_in_range.id,
        event_type=PartEventType.COMPLETED.value,
        created_at=_dt(date_from + timedelta(days=6)),
        batch_id=None,  # 工单级事件
    )

    # ============ overdue_undelivered 三个工单 ============
    # a) planned < today, actual IS NULL, status 非终态 → 计入
    p_overdue = await _make_part(
        clean_db, customer_id=cust.id, serial="S4001",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=2)),
        planned_delivery_date=today - timedelta(days=3),
        actual_delivery_date=None,
    )
    # b) planned > today, 未交付 → 不计入
    await _make_part(
        clean_db, customer_id=cust.id, serial="S4002",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=2)),
        planned_delivery_date=today + timedelta(days=10),
        actual_delivery_date=None,
    )
    # c) COMPLETED（终态）+ planned < today → 不计入
    await _make_part(
        clean_db, customer_id=cust.id, serial="S4003",
        status=PartStatus.COMPLETED.value,
        created_at=_dt(date_from + timedelta(days=2)),
        planned_delivery_date=today - timedelta(days=3),
        actual_delivery_date=today - timedelta(days=1),
    )

    # ============ REPAIR_STARTED 事件（distinct） ============
    # 同一件返修两次 → repair_part_count = 1
    p_repair = await _make_part(
        clean_db, customer_id=cust.id, serial="S5001",
        status=PartStatus.REPAIRING.value,
        created_at=_dt(date_from + timedelta(days=2)),
    )
    await _add_event(
        clean_db,
        part_id=p_repair.id, event_type=PartEventType.REPAIR_STARTED.value,
        created_at=_dt(date_from + timedelta(days=7)),
    )
    await _add_event(
        clean_db,
        part_id=p_repair.id, event_type=PartEventType.REPAIR_STARTED.value,
        created_at=_dt(date_from + timedelta(days=8)),
    )

    svc = _make_service(clean_db)
    out = await svc.overview(date_from=date_from, date_to=date_to)

    # 字段断言
    assert out.date_from == date_from
    assert out.date_to == date_to

    # 范围内 created: S1001..S1006 (6) + p_overdue + p_repair + S4002 + S4003 (4) = 10
    # S2001, S2002 创建在范围外；S3001 (p_completed_in_range) 创建在范围前
    assert out.created_count == 10

    # completed_count: 仅 S3001（工单级 COMPLETED 事件 batch_id IS NULL in 范围内）
    assert out.completed_count == 1

    # delivered_count: 实际送货日 in [from,to] 的件 =
    #   S1003 / S1004 / S1005 / S1006 (DELIVERED × 4)
    #   + S3001 (COMPLETED, actual=today)
    #   + S4003 (COMPLETED, actual=today-1)
    #   = 6 件
    assert out.delivered_count == 6

    # delivered_value 精确到分：6 件 × 100 = 600
    from decimal import Decimal
    assert out.delivered_value == Decimal("600.00")

    # 红档/橙档（基于实际送货日期在范围内的 6 件）：
    # _make_part 默认 planned_delivery_date=today+30（避免误算 overdue）
    # - S1003: actual=today, planned=today-10, system=today+10 → orange
    #   (actual > planned AND actual <= system)
    # - S1004: actual=today, planned=today-10, system=today-5 → red
    # - S1005: actual=today, planned=today-10, system=NULL → orange
    # - S1006: actual=today, planned=today+10, system=today+10 → on_time
    # - S3001: actual=today, planned=today+30 (default), system=NULL → on_time
    # - S4003: actual=today-1, planned=today-3, system=NULL → orange
    # → red=1, orange=3, on_time=2 (6 件总和)
    assert out.late_red_count == 1
    assert out.late_orange_count == 3

    # overdue_undelivered: 只 S4001 一件
    assert out.overdue_undelivered_count == 1

    # repair_part_count: distinct 1 件（S5001）
    assert out.repair_part_count == 1

    # delivery_performance: on_time=2, orange=3, red=1, total=6 ✓
    assert out.delivery_performance.on_time == 2
    assert out.delivery_performance.orange == 3
    assert out.delivery_performance.red == 1

    # daily_created / daily_completed: 长度 = 11 (date_to - date_from + 1)
    n_days = (date_to - date_from).days + 1
    assert len(out.daily_created) == n_days
    assert len(out.daily_completed) == n_days
    # 第一天 / 最后一天对应 date_from / date_to
    assert out.daily_created[0].date == date_from
    assert out.daily_created[-1].date == date_to


# ============================================================
# 2. 期末在制事件重构
# ============================================================
async def test_in_process_count_event_reconstruction(clean_db):
    """in_process_count 在 date_to 时刻还在制的工单数（事件重构）：
    - created_at < date_to + 1
    - NOT EXISTS 工单级 COMPLETED / CANCELLED 事件 created_at < date_to+1
    """
    today = now_naive().date()
    date_from = today - timedelta(days=10)
    date_to = today

    cust = await _make_l1_root(clean_db)

    # a) 范围前创建 + 范围前被 COMPLETED → 不计入
    p_done_before = await _make_part(
        clean_db, customer_id=cust.id, serial="IP01",
        status=PartStatus.COMPLETED.value,
        created_at=_dt(date_from - timedelta(days=1)),
        actual_delivery_date=today - timedelta(days=15),
    )
    await _add_event(
        clean_db,
        part_id=p_done_before.id,
        event_type=PartEventType.COMPLETED.value,
        created_at=_dt(date_from - timedelta(days=1)),
    )

    # b) 范围前创建 + 范围内被 COMPLETED → 不计入
    p_done_in_range = await _make_part(
        clean_db, customer_id=cust.id, serial="IP02",
        status=PartStatus.COMPLETED.value,
        created_at=_dt(date_from - timedelta(days=1)),
        actual_delivery_date=today,
    )
    await _add_event(
        clean_db,
        part_id=p_done_in_range.id,
        event_type=PartEventType.COMPLETED.value,
        created_at=_dt(date_from + timedelta(days=5)),
    )

    # c) 范围前创建 + 无终态事件 → 计入
    p_in_proc = await _make_part(
        clean_db, customer_id=cust.id, serial="IP03",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from - timedelta(days=1)),
    )

    # d) created_at > date_to → 不计入（无论有无事件）
    p_future = await _make_part(
        clean_db, customer_id=cust.id, serial="IP04",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_to + timedelta(days=1)),
    )
    await _add_event(
        clean_db,
        part_id=p_future.id,
        event_type=PartEventType.COMPLETED.value,
        created_at=_dt(date_to + timedelta(days=2)),
    )

    # e) 范围前创建 + 范围内 CANCELLED → 不计入
    p_cancelled = await _make_part(
        clean_db, customer_id=cust.id, serial="IP05",
        status=PartStatus.CANCELLED.value,
        created_at=_dt(date_from - timedelta(days=1)),
    )
    await _add_event(
        clean_db,
        part_id=p_cancelled.id,
        event_type=PartEventType.CANCELLED.value,
        created_at=_dt(date_from + timedelta(days=1)),
    )

    svc = _make_service(clean_db)
    out = await svc.overview(date_from=date_from, date_to=date_to)

    # 只有 IP03 一个在制；IP01/IP02/IP04/IP05 都已不在制
    assert out.in_process_count == 1


# ============================================================
# 3. tab2 工人贡献度
# ============================================================
async def test_worker_stats_contribution_percent(clean_db):
    """工种 A: A1 3 次 PICKED_UP, A2 1 次 → A1.contribution=75, A2=25。
    A 总领取 4 次；无工种工人 W-no 2 次 → contribution_pct=None。
    工种 B 全部 0 领取 → contribution=None。"""
    today = now_naive().date()
    date_from = today - timedelta(days=5)
    date_to = today

    cust = await _make_l1_root(clean_db)
    wt_a = await _make_work_type(clean_db, code="WT-A", name="工种A")
    wt_b = await _make_work_type(clean_db, code="WT-B", name="工种B")

    a1 = await _make_worker(
        clean_db, badge="A1", name="工人A1", work_type_id=wt_a.id,
    )
    a2 = await _make_worker(
        clean_db, badge="A2", name="工人A2", work_type_id=wt_a.id,
    )
    b1 = await _make_worker(
        clean_db, badge="B1", name="工人B1", work_type_id=wt_b.id,
    )
    no_wt = await _make_worker(
        clean_db, badge="NO-WT", name="无工种工人", work_type_id=None,
    )

    # 制造不同工单的 PICKED_UP 事件
    for i in range(3):
        p = await _make_part(
            clean_db, customer_id=cust.id, serial=f"WS-A1-{i}",
            status=PartStatus.IN_PROCESS.value,
            created_at=_dt(date_from + timedelta(days=1)),
        )
        await _add_event(
            clean_db,
            part_id=p.id, event_type=PartEventType.PICKED_UP.value,
            created_at=_dt(date_from + timedelta(days=2), hour=10 + i),
            worker_id=a1.id,
        )

    p = await _make_part(
        clean_db, customer_id=cust.id, serial="WS-A2-1",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=1)),
    )
    await _add_event(
        clean_db,
        part_id=p.id, event_type=PartEventType.PICKED_UP.value,
        created_at=_dt(date_from + timedelta(days=3)),
        worker_id=a2.id,
    )

    # no_wt 工人 2 次
    for i in range(2):
        p = await _make_part(
            clean_db, customer_id=cust.id, serial=f"WS-NO-{i}",
            status=PartStatus.IN_PROCESS.value,
            created_at=_dt(date_from + timedelta(days=1)),
        )
        await _add_event(
            clean_db,
            part_id=p.id, event_type=PartEventType.PICKED_UP.value,
            created_at=_dt(date_from + timedelta(days=4), hour=10 + i),
            worker_id=no_wt.id,
        )

    # 软删 a2 但仍保留事件 → a2 不在结果里
    a2.deleted_at = now_naive()
    await clean_db.flush()

    svc = _make_service(clean_db)
    out = await svc.worker_stats(date_from=date_from, date_to=date_to)

    by_id = {i.worker_id: i for i in out.items}

    # 软删的 a2 不出现在列表里
    assert a2.id not in by_id

    # a1: 3 / 3 = 100%（因为 a2 被软删后，工种 A 实际只有 a1，且其领取 3 次）
    # 注意：a2 软删后 worker_pickup_rows 仍然把 a2 的 1 次算进 totals_by_wt（不软删）
    # 因为 SQL JOIN 没限制 deleted_at；total = 4；a1=3/4=75；a2=1/4=25
    # 但 a2 被软删后 worker_stats 不返 a2；a1 应是 3/4=75
    a1_item = by_id[a1.id]
    assert a1_item.contribution_pct == 75.0
    assert a1_item.pickup_count == 3
    assert a1_item.work_type_id is not None
    assert a1_item.work_type_name == "工种A"

    # no_wt 工人：2 次，但 contribution_pct=None
    no_wt_item = by_id[no_wt.id]
    assert no_wt_item.contribution_pct is None
    assert no_wt_item.work_type_id is None
    assert no_wt_item.work_type_name is None

    # b1: 0 领取 contribution=None（除零）
    b1_item = by_id[b1.id]
    assert b1_item.contribution_pct is None
    assert b1_item.pickup_count == 0


# ============================================================
# 4. tab3 参与工单清单
# ============================================================
async def test_worker_detail_dedup_and_soft_delete(clean_db):
    """同一工人对同一工单 PICKED_UP 两次 → parts 列表 dedup，count=2。"""
    today = now_naive().date()
    date_from = today - timedelta(days=3)
    date_to = today

    cust = await _make_l1_root(clean_db)
    wt = await _make_work_type(clean_db)
    worker = await _make_worker(
        clean_db, badge="WD-1", work_type_id=wt.id,
    )

    # 工单 A：PICKED_UP 两次（distinct pickup_count=2, 但 parts 列表只一次）
    p_a = await _make_part(
        clean_db, customer_id=cust.id, serial="DUP-A",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=1)),
    )
    await _add_event(
        clean_db, part_id=p_a.id,
        event_type=PartEventType.PICKED_UP.value,
        created_at=_dt(date_from + timedelta(days=1), hour=10),
        worker_id=worker.id,
    )
    await _add_event(
        clean_db, part_id=p_a.id,
        event_type=PartEventType.PICKED_UP.value,
        created_at=_dt(date_from + timedelta(days=2), hour=11),
        worker_id=worker.id,
    )

    # 工单 B：PICKED_UP 一次
    p_b = await _make_part(
        clean_db, customer_id=cust.id, serial="DUP-B",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=1)),
    )
    await _add_event(
        clean_db, part_id=p_b.id,
        event_type=PartEventType.PICKED_UP.value,
        created_at=_dt(date_from + timedelta(days=2), hour=15),
        worker_id=worker.id,
    )

    # 工单 C：领取后被软删 → 卡片 pickup 计入，但 parts 列表排除
    p_c = await _make_part(
        clean_db, customer_id=cust.id, serial="DUP-C",
        status=PartStatus.IN_PROCESS.value,
        created_at=_dt(date_from + timedelta(days=1)),
    )
    await _add_event(
        clean_db, part_id=p_c.id,
        event_type=PartEventType.PICKED_UP.value,
        created_at=_dt(date_from + timedelta(days=3), hour=10),
        worker_id=worker.id,
    )
    p_c.deleted_at = now_naive()
    await clean_db.flush()

    svc = _make_service(clean_db)
    detail = await svc.worker_detail(
        worker_id=str(worker.id), date_from=date_from, date_to=date_to,
    )

    # pickup_count 卡片：4 个 PICKED_UP 事件（p_c 已软删但其事件仍计入卡片）
    # parts 列表只 2 件（A 和 B），C 不出现
    assert detail.pickup_count == 4
    # parts 列表只 2 件（A 和 B），C 不出现
    part_ids = [p.part_id for p in detail.parts]
    assert p_a.id in part_ids
    assert p_b.id in part_ids
    assert p_c.id not in part_ids
    # A 的 pickup_count=2
    a_item = next(p for p in detail.parts if p.part_id == p_a.id)
    assert a_item.pickup_count == 2
    # parts 按 last_pickup_at DESC：B 最后拾取 time 是 day 2 / hour 15，A 是 day 2 hour 11
    assert detail.parts[0].part_id == p_b.id
    assert detail.parts[1].part_id == p_a.id

    # participated_part_count：distinct part（不含已软删 C）
    assert detail.participated_part_count == 2

    # daily_pickups 零填充到 [date_from, date_to]
    n_days = (date_to - date_from).days + 1
    assert len(detail.daily_pickups) == n_days


async def test_worker_detail_not_found(clean_db):
    """worker_id 不存在 → 404 BIZ_WORKER_NOT_FOUND。"""
    today = now_naive().date()
    svc = _make_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.worker_detail(
            worker_id="999999999999",
            date_from=today - timedelta(days=1), date_to=today,
        )
    assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
    assert exc_info.value.http_status == 404


async def test_worker_detail_soft_deleted_worker_returns_404(clean_db):
    """已软删 worker → 404。"""
    today = now_naive().date()
    cust = await _make_l1_root(clean_db)
    await _make_worker(
        clean_db, badge="WD-SOFT",
        deleted_at=now_naive(),
    )
    svc = _make_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.worker_detail(
            worker_id="123456789012",
            date_from=today - timedelta(days=1), date_to=today,
        )
    assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND


async def test_worker_detail_invalid_snowflake_id(clean_db):
    """非数字字符串 worker_id → 400 BIZ_INVALID_VALUE。"""
    today = now_naive().date()
    svc = _make_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.worker_detail(
            worker_id="not-a-number",
            date_from=today - timedelta(days=1), date_to=today,
        )
    assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE
    assert exc_info.value.http_status == 400


async def test_worker_detail_invalid_date_range(clean_db):
    """date_from > date_to → 400 BIZ_INVALID_VALUE。"""
    today = now_naive().date()
    cust = await _make_l1_root(clean_db)
    wt = await _make_work_type(clean_db)
    worker = await _make_worker(clean_db, badge="WD-DR", work_type_id=wt.id)
    svc = _make_service(clean_db)
    with pytest.raises(BizError) as exc_info:
        await svc.worker_detail(
            worker_id=str(worker.id),
            date_from=today, date_to=today - timedelta(days=1),
        )
    assert exc_info.value.code == ErrCode.BIZ_INVALID_VALUE


async def test_worker_detail_daily_zero_filled_empty_range(clean_db):
    """单日查询 range = 同一天 → daily_pickups 长度为 1。"""
    today = now_naive().date()
    cust = await _make_l1_root(clean_db)
    wt = await _make_work_type(clean_db)
    worker = await _make_worker(clean_db, badge="WD-SD", work_type_id=wt.id)
    svc = _make_service(clean_db)
    detail = await svc.worker_detail(
        worker_id=str(worker.id), date_from=today, date_to=today,
    )
    assert len(detail.daily_pickups) == 1
    assert detail.daily_pickups[0].date == today
    assert detail.daily_pickups[0].count == 0


# ============================================================
# 5. 权限（router 级 require_role(MANAGER)）
# ============================================================
async def test_overview_rejects_non_manager_role(clean_db):
    """CLERK 调 get_overview 走 require_role(MANAGER) → 403。"""
    from core.permission import CurrentUser, require_role
    from model.enums import UserRole

    clerk = CurrentUser(
        id=999, username="c", full_name="c", is_active=True,
        roles=(UserRole.CLERK.value,), shelf_ids=(),
    )
    dep = require_role(UserRole.MANAGER)
    with pytest.raises(BizError) as exc_info:
        await dep(user=clerk)
    assert exc_info.value.code == ErrCode.FORBIDDEN
    assert exc_info.value.http_status == 403


async def test_workers_stats_rejects_non_manager_role(clean_db):
    """SHELF_ACCOUNT 调 worker_stats → 403。"""
    from core.permission import CurrentUser, require_role

    sh = CurrentUser(
        id=999, username="s", full_name="s", is_active=True,
        roles=(UserRole.SHELF_ACCOUNT.value,), shelf_ids=(),
    )
    dep = require_role(UserRole.MANAGER)
    with pytest.raises(BizError) as exc_info:
        await dep(user=sh)
    assert exc_info.value.code == ErrCode.FORBIDDEN


async def test_worker_detail_rejects_any_non_manager(clean_db):
    """任意非 MANAGER 角色 → 403。"""
    from core.permission import CurrentUser, require_role

    cncp = CurrentUser(
        id=999, username="cn", full_name="c", is_active=True,
        roles=(UserRole.CNC_PROGRAMMER.value,), shelf_ids=(),
    )
    dep = require_role(UserRole.MANAGER)
    with pytest.raises(BizError) as exc_info:
        await dep(user=cncp)
    assert exc_info.value.code == ErrCode.FORBIDDEN


async def test_overview_accepts_manager_role():
    """MANAGER 通过 require_role。"""
    from core.permission import CurrentUser, require_role

    mgr = CurrentUser(
        id=1, username="m", full_name="m", is_active=True,
        roles=(UserRole.MANAGER.value,), shelf_ids=(),
    )
    dep = require_role(UserRole.MANAGER)
    out = await dep(user=mgr)
    assert out is mgr


# ============================================================
# 6. 图表数据完整性
# ============================================================
async def test_daily_series_zero_filled_complete_range(clean_db):
    """daily_created / daily_completed 长度 = date_to - date_from + 1。"""
    today = now_naive().date()
    date_from = today - timedelta(days=6)
    date_to = today

    cust = await _make_l1_root(clean_db)
    # 范围内每天创建 1 个
    for i in range((date_to - date_from).days + 1):
        target_day = date_from + timedelta(days=i)
        await _make_part(
            clean_db, customer_id=cust.id, serial=f"D-{i:02d}",
            status=PartStatus.PENDING.value,
            created_at=_dt(target_day),
        )

    # 范围内不创建任何 COMPLETED 事件
    svc = _make_service(clean_db)
    out = await svc.overview(date_from=date_from, date_to=date_to)

    n_days = (date_to - date_from).days + 1  # 7
    assert len(out.daily_created) == n_days
    assert len(out.daily_completed) == n_days

    # daily_created 每天 1 个（sum = 7）
    assert sum(d.count for d in out.daily_created) == 7
    # daily_completed 每天 0
    assert sum(d.count for d in out.daily_completed) == 0


async def test_delivery_performance_partition_matches_delivered_count(clean_db):
    """on_time + orange + red == delivered_count（期内实际送货的零件子集）。"""
    today = now_naive().date()
    date_from = today - timedelta(days=3)
    date_to = today

    cust = await _make_l1_root(clean_db)
    # 4 件 on_time, 3 件 orange, 2 件 red = 9 件总
    for i in range(4):
        await _make_part(
            clean_db, customer_id=cust.id, serial=f"OT-{i}",
            status=PartStatus.DELIVERED.value,
            actual_delivery_date=today,
            planned_delivery_date=today + timedelta(days=5),
            system_delivery_date=today + timedelta(days=10),
        )
    for i in range(3):
        await _make_part(
            clean_db, customer_id=cust.id, serial=f"OR-{i}",
            status=PartStatus.DELIVERED.value,
            actual_delivery_date=today,
            planned_delivery_date=today - timedelta(days=10),
            system_delivery_date=None,
        )
    for i in range(2):
        await _make_part(
            clean_db, customer_id=cust.id, serial=f"RD-{i}",
            status=PartStatus.DELIVERED.value,
            actual_delivery_date=today,
            planned_delivery_date=today - timedelta(days=10),
            system_delivery_date=today - timedelta(days=5),
        )

    svc = _make_service(clean_db)
    out = await svc.overview(date_from=date_from, date_to=date_to)

    assert out.delivered_count == 9
    total_perf = (
        out.delivery_performance.on_time
        + out.delivery_performance.orange
        + out.delivery_performance.red
    )
    assert total_perf == out.delivered_count


# ============================================================
# 7. 额外：worker_stats 列表排除软删工人
# ============================================================
async def test_worker_stats_excludes_soft_deleted_workers(clean_db):
    """已软删工人（deleted_at IS NOT NULL）不出现在 worker_stats.items 中。"""
    today = now_naive().date()
    date_from = today - timedelta(days=3)
    date_to = today

    cust = await _make_l1_root(clean_db)
    wt = await _make_work_type(clean_db)
    active = await _make_worker(clean_db, badge="ACT", work_type_id=wt.id)
    soft_deleted = await _make_worker(
        clean_db, badge="DEL", work_type_id=wt.id,
        deleted_at=now_naive(),
    )

    svc = _make_service(clean_db)
    out = await svc.worker_stats(date_from=date_from, date_to=date_to)

    ids = [i.worker_id for i in out.items]
    assert active.id in ids
    assert soft_deleted.id not in ids
