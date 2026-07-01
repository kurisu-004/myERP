"""数据大屏快照构建。

- ready_queue: status=READY 的零件，按 *加急优先 → 交期近优先* 排序，前 top_n。
- in_process: status=IN_PROCESS 的零件，按 id 倒序；含 current_worker.name。
- upcoming_delivery: 未来 7 天（含今天）每天的待交零件数，
  排除 COMPLETED / CANCELLED 终态；零计数日期也补齐，返回固定 7 条。

DB 不存 ENUM，所以这里用 `PartStatus.<X>.value` 字面量比较。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart, TPartEvent, TWorker
from model.enums import PartEventType, PartStatus


DASHBOARD_TOP_N = 20


@dataclass
class DashboardSnapshot:
    """数据大屏快照结构（dict-like，便于 json 序列化）。"""

    ready_queue: list[dict[str, Any]]
    in_process: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_queue": self.ready_queue,
            "in_process": self.in_process,
        }


async def build_snapshot(
    session: AsyncSession, top_n: int = DASHBOARD_TOP_N
) -> dict[str, Any]:
    """异步构建一次完整快照。

    返回 `{"ready_queue": [...], "in_process": [...],
            "upcoming_delivery": [...], "ts": iso8601}`。
    """
    ready_parts = await _fetch_ready(session, top_n)
    in_process_parts = await _fetch_in_process(session, top_n)

    # 批量取客户路径
    cust_ids = list({p.customer_id for p in ready_parts + in_process_parts})
    cust_map = await _fetch_customer_path(session, cust_ids)

    # 每个 in_process 零件最近一次 PICKED_UP 时间
    picked_at_map = await _picked_up_at_map(
        session, [p.id for p in in_process_parts]
    )

    ready_items = [_to_dict(p, cust_map, picked_up_at=None) for p in ready_parts]
    in_process_items = [
        _to_dict(p, cust_map, picked_up_at=picked_at_map.get(p.id))
        for p in in_process_parts
    ]

    upcoming_delivery = await _fetch_upcoming_delivery(session, days=7)

    out = {
        "ready_queue": ready_items,
        "in_process": in_process_items,
        "upcoming_delivery": upcoming_delivery,
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    return out


async def _fetch_ready(
    session: AsyncSession, top_n: int
) -> list[TPart]:
    """READY 队列：加急优先 → 交期近优先 → id 兜底。

    - is_urgent DESC：True(1) 排在 False(0) 前；
    - planned_delivery_date ASC：交期近的先；
    - id ASC：同交期时按创建先后稳定排序。
    """
    stmt = (
        select(TPart)
        .where(TPart.status == PartStatus.READY.value)
        .where(TPart.deleted_at.is_(None))
        .order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.asc(),
        )
        .limit(top_n)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _fetch_upcoming_delivery(
    session: AsyncSession, days: int = 7
) -> list[dict[str, Any]]:
    """未来 `days` 天（含今天）按计划交期分桶的待交零件数。

    - 排除终态（COMPLETED / CANCELLED）；
    - 排除软删记录；
    - 零计数日期也补齐返回固定 `days` 条，保证前端 7 根柱子稳定；
    - 返回 `[{"date": "YYYY-MM-DD", "count": int}, ...]`，按 date 升序。
    """
    today = date.today()
    end = today + timedelta(days=days - 1)
    # 用 SQL 端参数化，避免函数调用在 WHERE 里无法走索引
    stmt = (
        select(TPart.planned_delivery_date, func.count(TPart.id))
        .where(TPart.deleted_at.is_(None))
        .where(
            TPart.status.notin_(
                [PartStatus.COMPLETED.value, PartStatus.CANCELLED.value]
            )
        )
        .where(TPart.planned_delivery_date >= today)
        .where(TPart.planned_delivery_date <= end)
        .group_by(TPart.planned_delivery_date)
    )
    rows = (await session.execute(stmt)).all()
    bucket: dict[date, int] = {d: int(n) for d, n in rows}
    out: list[dict[str, Any]] = []
    for offset in range(days):
        d = today + timedelta(days=offset)
        out.append({"date": d.isoformat(), "count": bucket.get(d, 0)})
    return out


async def _fetch_in_process(
    session: AsyncSession, top_n: int
) -> list[TPart]:
    stmt = (
        select(TPart)
        .where(TPart.status == PartStatus.IN_PROCESS.value)
        .where(TPart.deleted_at.is_(None))
        .order_by(TPart.id.desc())
        .limit(top_n)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _fetch_customer_path(
    session: AsyncSession, cust_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """取客户全路径（一级 / 二级）。"""
    if not cust_ids:
        return {}
    from model import TCustomer

    stmt = select(TCustomer).where(TCustomer.id.in_(cust_ids))
    customers = list((await session.execute(stmt)).scalars().all())
    parent_ids = [c.parent_id for c in customers if c.parent_id]
    parent_map: dict[int, TCustomer] = {}
    if parent_ids:
        pstmt = select(TCustomer).where(TCustomer.id.in_(parent_ids))
        parents = list((await session.execute(pstmt)).scalars().all())
        parent_map = {p.id: p for p in parents}

    out: dict[int, dict[str, Any]] = {}
    for c in customers:
        parent = parent_map.get(c.parent_id) if c.parent_id else None
        parent_name = parent.name if parent else None
        child_name = c.name
        if parent_name and child_name:
            path = f"{parent_name} / {child_name}"
        elif child_name:
            path = child_name
        else:
            path = parent_name
        out[c.id] = {"customer_name": child_name, "customer_path": path}
    return out


async def _picked_up_at_map(
    session: AsyncSession, part_ids: list[int]
) -> dict[int, Any]:
    """每个 in_process 零件最近一次 PICKED_UP 事件的 created_at。"""
    if not part_ids:
        return {}
    # PG: 用 DISTINCT ON 拿每个 part_id 的最新一条
    stmt = (
        select(TPartEvent.part_id, TPartEvent.created_at)
        .where(TPartEvent.part_id.in_(part_ids))
        .where(TPartEvent.event_type == PartEventType.PICKED_UP.value)
        .order_by(TPartEvent.part_id.asc(), TPartEvent.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return {pid: ts for pid, ts in rows}


async def _fetch_worker_names(
    session: AsyncSession, worker_ids: list[str]
) -> dict[str, str]:
    """每个 worker_id → name。

    worker_ids 接收字符串（与 _to_dict 输出对齐），查询前转回 int——
    PostgreSQL 不会自动把 varchar cast 成 bigint，直接传 str 会导致
    `operator does not exist: bigint = character varying`。
    """
    if not worker_ids:
        return {}
    int_ids = [int(wid) for wid in worker_ids]
    stmt = select(TWorker).where(TWorker.id.in_(int_ids))
    workers = list((await session.execute(stmt)).scalars().all())
    return {str(w.id): w.name for w in workers}


def _to_dict(
    part: TPart,
    cust_map: dict[int, dict[str, Any]],
    *,
    picked_up_at: Any | None,
) -> dict[str, Any]:
    cust_info = cust_map.get(part.customer_id, {})
    # id 类字段全部序列化为字符串，避免 JS Number.MAX_SAFE_INTEGER 精度截断
    # 见 schema/_types.py IdStr 的设计意图
    return {
        "id": str(part.id),
        "serial_no": part.serial_no,
        "name": part.name,
        "drawing_no": part.drawing_no,
        "quantity": part.quantity,
        "is_urgent": bool(part.is_urgent),
        "planned_delivery_date": part.planned_delivery_date.isoformat()
        if part.planned_delivery_date
        else None,
        "released_at": part.released_at.isoformat() + "Z"
        if part.released_at
        else None,
        "picked_up_at": picked_up_at.isoformat() + "Z" if picked_up_at else None,
        "current_worker_id": (
            str(part.current_worker_id) if part.current_worker_id else None
        ),
        "customer_id": str(part.customer_id) if part.customer_id else None,
        "customer_name": cust_info.get("customer_name"),
        "customer_path": cust_info.get("customer_path"),
    }


async def build_snapshot_with_workers(
    session: AsyncSession, top_n: int = DASHBOARD_TOP_N
) -> dict[str, Any]:
    """在 build_snapshot 基础上，再补 worker_name。"""
    snap = await build_snapshot(session, top_n)
    worker_ids = [
        item.get("current_worker_id")
        for item in snap["in_process"]
        if item.get("current_worker_id")
    ]
    worker_name_map = await _fetch_worker_names(session, worker_ids)
    for item in snap["in_process"]:
        wid = item.get("current_worker_id")
        item["worker_name"] = worker_name_map.get(wid) if wid else None
    return snap