"""数据大屏快照构建。

- on_production_shelves: status=IN_PROCESS 且 holder 在生产货架上的零件，
  按 shelf 分组，按 planned_delivery_date ASC、id ASC 排序。
- on_inspection_shelves: status=INSPECTION 且 holder 在品检货架上的零件（扁平）。
- in_process:             status=IN_PROCESS 且 holder 是工人的零件。
- upcoming_delivery:      未来 7 天（含今天）按计划交期分桶的待交数，
                          排除 COMPLETED / CANCELLED 终态；
                          零计数日期也补齐，返回固定 7 条。

DB 不存 ENUM，所以这里用 `PartStatus.<X>.value` 字面量比较。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_shanghai_iso
from model import TPart, TPartEvent, TProcess, TShelf, TWorker
from model.enums import (
    PartEventType,
    PartStatus,
    ShelfZone,
)


DASHBOARD_TOP_N = 20


@dataclass
class DashboardSnapshot:
    """数据大屏快照结构（dict-like，便于 json 序列化）。"""

    on_production_shelves: list[dict[str, Any]]
    on_inspection_shelves: list[dict[str, Any]]
    in_process: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "on_production_shelves": self.on_production_shelves,
            "on_inspection_shelves": self.on_inspection_shelves,
            "in_process": self.in_process,
        }


async def build_snapshot(
    session: AsyncSession, top_n: int = DASHBOARD_TOP_N
) -> dict[str, Any]:
    """异步构建一次完整快照。

    返回 `{on_production_shelves, on_inspection_shelves, in_process,
            upcoming_delivery, ts}`。

    生产区货架特殊处理：先查所有 active 货架（即使没零件也带出来），
    再用 SQL `ROW_NUMBER() OVER (PARTITION BY current_holder_id)` 对每架
    取前 10 件（加急优先 + 交期近优先）。空货架最后用 items=[] 补齐。
    """
    # 1) 所有 active 生产区货架（即使没零件）
    all_prod_stmt = (
        select(TShelf)
        .where(
            TShelf.zone == ShelfZone.PRODUCTION.value,
            TShelf.deleted_at.is_(None),
            TShelf.is_active.is_(True),
        )
        .order_by(TShelf.code.asc())
    )
    all_prod_shelves = list((await session.execute(all_prod_stmt)).scalars().all())
    active_prod_ids = [s.id for s in all_prod_shelves]

    # 2) 查所有 active 生产区货架上的 IN_PROCESS 零件（先一次性取全，Python 端按货架分组后每架取前 10）
    on_prod_parts: list[TPart] = []
    if active_prod_ids:
        stmt = (
            select(TPart)
            .where(
                TPart.status == PartStatus.IN_PROCESS.value,
                TPart.deleted_at.is_(None),
                TPart.current_holder_id.in_(active_prod_ids),
            )
            .order_by(
                TPart.current_holder_id.asc(),
                TPart.is_urgent.desc(),
                TPart.planned_delivery_date.asc(),
                TPart.id.asc(),
            )
        )
        on_prod_parts = list((await session.execute(stmt)).scalars().all())

    on_insp_parts = await _fetch_on_zone_shelves(
        session, zone=ShelfZone.INSPECTION.value, top_n=top_n
    )
    worker_parts = await _fetch_in_process_worker(session, top_n)

    # 批量取客户路径
    cust_ids = list(
        {p.customer_id for p in on_prod_parts + on_insp_parts + worker_parts}
    )
    cust_map = await _fetch_customer_path(session, cust_ids)

    # 批量取工人名字（仅 in_process 部分）
    worker_ids = [
        p.current_holder_id for p in worker_parts if p.current_holder_id
    ]
    worker_name_map = await _fetch_worker_names(session, worker_ids)

    # 批量取工序名（适用三个区段的所有零件；Dashboard 大屏显示「下一工序」）
    process_ids = list({
        p.next_process_id
        for p in on_prod_parts + on_insp_parts + worker_parts
        if p.next_process_id is not None
    })
    process_name_map = await _fetch_process_names(session, process_ids)

    # 品检区维持原状：只查有 holder 指向的货架
    insp_shelf_ids = {
        p.current_holder_id
        for p in on_insp_parts
        if p.current_holder_id
    }
    insp_shelf_map = await _fetch_shelves_by_ids(session, list(insp_shelf_ids))

    # 批量取最近一次 PICKED_UP 时间
    picked_at_map = await _picked_up_at_map(
        session, [p.id for p in worker_parts]
    )

    # 3) 拼装：按 current_holder_id 分桶，每架取前 10 件；所有 active 货架都出现
    parts_by_shelf: dict[int, list[TPart]] = {}
    for p in on_prod_parts:
        sid = p.current_holder_id
        if sid is None:
            continue
        parts_by_shelf.setdefault(sid, []).append(p)

    on_prod_groups: list[dict[str, Any]] = []
    for s in all_prod_shelves:
        items = parts_by_shelf.get(s.id, [])[:10]
        on_prod_groups.append({
            "shelf_id": str(s.id),
            "shelf_code": s.code,
            "shelf_name": s.name,
            "items": [
                _to_dict(
                    p,
                    cust_map=cust_map,
                    holder_kind="shelf",
                    shelf_code=s.code,
                    placed_at=getattr(p, "placed_at", None),
                    process_map=process_name_map,
                )
                for p in items
            ],
        })

    on_insp_items = [
        _to_dict(
            p,
            cust_map=cust_map,
            holder_kind="shelf",
            shelf_code=(insp_shelf_map[p.current_holder_id].code
                        if p.current_holder_id in insp_shelf_map else None),
            placed_at=getattr(p, "placed_at", None),
            process_map=process_name_map,
        )
        for p in on_insp_parts
    ]
    in_process_items = [
        _to_dict(
            p,
            cust_map=cust_map,
            holder_kind="worker",
            worker_name=worker_name_map.get(p.current_holder_id),
            picked_up_at=picked_at_map.get(p.id),
            process_map=process_name_map,
        )
        for p in worker_parts
    ]

    upcoming_delivery = await _fetch_upcoming_delivery(session, days=7)

    out = {
        "on_production_shelves": on_prod_groups,
        "on_inspection_shelves": on_insp_items,
        "in_process": in_process_items,
        "upcoming_delivery": upcoming_delivery,
        "ts": now_shanghai_iso(),
    }
    return out


# ============================================================
# 抓取：按货架 zone 列出 IN_PROCESS / INSPECTION 零件
# ============================================================
async def _fetch_on_zone_shelves(
    session: AsyncSession, *, zone: str, top_n: int
) -> list[TPart]:
    """status ∈ {IN_PROCESS, INSPECTION} ∩ holder ∈ t_shelf(z=zone)。"""
    subq = select(TShelf.id).where(
        TShelf.zone == zone,
        TShelf.deleted_at.is_(None),
        TShelf.is_active.is_(True),
    )
    status_value = (
        PartStatus.IN_PROCESS.value
        if zone == ShelfZone.PRODUCTION.value
        else PartStatus.INSPECTION.value
    )
    stmt = (
        select(TPart)
        .where(TPart.status == status_value)
        .where(TPart.deleted_at.is_(None))
        .where(TPart.current_holder_id.in_(subq))
        .order_by(
            TPart.current_holder_id.asc(),
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.asc(),
        )
        .limit(top_n)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _fetch_in_process_worker(
    session: AsyncSession, top_n: int
) -> list[TPart]:
    """IN_PROCESS 且 holder 在 t_worker(is_active) 集合内。"""
    worker_subq = select(TWorker.id).where(
        TWorker.deleted_at.is_(None),
        TWorker.is_active.is_(True),
    )
    stmt = (
        select(TPart)
        .where(TPart.status == PartStatus.IN_PROCESS.value)
        .where(TPart.deleted_at.is_(None))
        .where(TPart.current_holder_id.in_(worker_subq))
        .order_by(TPart.id.desc())
        .limit(top_n)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _fetch_upcoming_delivery(
    session: AsyncSession, days: int = 7
) -> list[dict[str, Any]]:
    """未来 days 天（含今天）按计划交期分桶的待交零件数。"""
    today = date.today()
    end = today + timedelta(days=days - 1)
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


async def _fetch_customer_path(
    session: AsyncSession, cust_ids: list[int]
) -> dict[int, dict[str, Any]]:
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


async def _fetch_shelves_by_ids(
    session: AsyncSession, ids: list[int]
) -> dict[int, TShelf]:
    if not ids:
        return {}
    stmt = select(TShelf).where(
        TShelf.id.in_(ids),
        TShelf.deleted_at.is_(None),
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {s.id: s for s in rows}


async def _picked_up_at_map(
    session: AsyncSession, part_ids: list[int]
) -> dict[int, Any]:
    if not part_ids:
        return {}
    stmt = (
        select(TPartEvent.part_id, TPartEvent.created_at)
        .where(TPartEvent.part_id.in_(part_ids))
        .where(TPartEvent.event_type == PartEventType.PICKED_UP.value)
        .order_by(TPartEvent.part_id.asc(), TPartEvent.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return {pid: ts for pid, ts in rows}


async def _fetch_worker_names(
    session: AsyncSession, worker_ids: list[int]
) -> dict[int, str]:
    if not worker_ids:
        return {}
    stmt = select(TWorker).where(
        TWorker.id.in_(worker_ids),
        TWorker.deleted_at.is_(None),
    )
    workers = list((await session.execute(stmt)).scalars().all())
    return {w.id: w.name for w in workers}


async def _fetch_process_names(
    session: AsyncSession, process_ids: list[int]
) -> dict[int, str]:
    """批量 `WHERE id IN (...)` 取工序名（Dashboard 大屏显示「下一工序」用）。

    与 _fetch_worker_names / _fetch_customer_path 同款：列表空短路、
    `deleted_at IS NULL` 过滤、单次 SELECT 一次性取回 Python 端拼 dict。
    这样避免在 async session 中触发 lazy load（M CLAUDE.md §13）。
    """
    if not process_ids:
        return {}
    stmt = select(TProcess).where(
        TProcess.id.in_(process_ids),
        TProcess.deleted_at.is_(None),
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {r.id: r.name for r in rows}


# ============================================================
# 拼装响应
# ============================================================
def _group_by_shelf(
    parts: list[TPart],
    shelf_map: dict[int, TShelf],
    cust_map: dict[int, dict[str, Any]],
    *,
    holder_kind: str,
    process_map: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """按 current_holder_id (= shelf.id) 分组并按 shelf 顺序返回。

    每组：`{shelf_id, shelf_code, shelf_name, items:[...]}`。
    items 已按 SQL 排序，再按 holder 分桶即可。
    """
    buckets: dict[int, list[TPart]] = {}
    order: list[int] = []
    for p in parts:
        sid = p.current_holder_id
        if sid is None:
            continue
        if sid not in buckets:
            buckets[sid] = []
            order.append(sid)
        buckets[sid].append(p)

    groups: list[dict[str, Any]] = []
    for sid in order:
        shelf = shelf_map.get(sid)
        if shelf is None:
            continue
        groups.append(
            {
                "shelf_id": str(shelf.id),
                "shelf_code": shelf.code,
                "shelf_name": shelf.name,
                "items": [
                    _to_dict(
                        p,
                        cust_map=cust_map,
                        holder_kind=holder_kind,
                        shelf_code=shelf.code,
                        placed_at=getattr(p, "placed_at", None),
                        process_map=process_map,
                    )
                    for p in buckets[sid]
                ],
            }
        )
    return groups


def _to_dict(
    part: TPart,
    *,
    cust_map: dict[int, dict[str, Any]],
    holder_kind: str | None,
    worker_name: str | None = None,
    picked_up_at: Any | None = None,
    shelf_code: str | None = None,
    placed_at: Any | None = None,
    process_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    cust_info = cust_map.get(part.customer_id, {})
    np_id = part.next_process_id
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
        "picked_up_at": picked_up_at.isoformat() + "Z" if picked_up_at else None,
        "current_holder_id": (
            str(part.current_holder_id) if part.current_holder_id else None
        ),
        "current_holder_kind": holder_kind,
        "shelf_code": shelf_code,
        "placed_at": placed_at.isoformat() + "Z" if placed_at else None,
        "customer_id": str(part.customer_id) if part.customer_id else None,
        "customer_name": cust_info.get("customer_name"),
        "customer_path": cust_info.get("customer_path"),
        # 下一工序：Dashboard 大屏直接显示，省一次前端 /processes 拉取
        "next_process_id": str(np_id) if np_id else None,
        "next_process_name": process_map.get(np_id) if (np_id and process_map) else None,
    }


async def build_snapshot_with_workers(
    session: AsyncSession, top_n: int = DASHBOARD_TOP_N
) -> dict[str, Any]:
    """`build_snapshot` 的薄包装，保留 ws 端点的调用契约。

    实际填充已在 `build_snapshot` 内完成（含 worker_name / shelf_code）。
    """
    return await build_snapshot(session, top_n)
