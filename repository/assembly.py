"""装配件仓储。

照搬 `PartRepository` 形状：构造取 session，方法按 CLAUDE.md §2
约定提供。
"""
from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TAssembly, TPart


class AssemblySortKey(str, enum.Enum):
    """装配体列表支持的排序字段。"""

    PLANNED_DELIVERY_DATE = "PLANNED_DELIVERY_DATE"
    REQUEST_DATE = "REQUEST_DATE"
    CREATED_AT = "CREATED_AT"
    SERIAL_NO = "SERIAL_NO"
    DRAWING_NO = "DRAWING_NO"
    NAME = "NAME"


AssemblySortDir = str  # "asc" | "desc"，保持简化的字符串


class AssemblyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, assembly: TAssembly) -> TAssembly:
        self.session.add(assembly)
        await self.session.flush()
        return assembly

    async def create_many(self, items: list[TAssembly]) -> list[TAssembly]:
        self.session.add_all(items)
        await self.session.flush()
        return items

    # ===== 单条查询 =====
    async def get_by_id(
        self, assembly_id: int, *, include_deleted: bool = False
    ) -> TAssembly | None:
        a = await self.session.get(TAssembly, assembly_id)
        if a is None:
            return None
        if not include_deleted and a.deleted_at is not None:
            return None
        return a

    async def get_by_drawing_no(
        self, drawing_no: str, *, include_deleted: bool = False
    ) -> TAssembly | None:
        stmt = select(TAssembly).where(TAssembly.drawing_no == drawing_no)
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        stmt = stmt.order_by(TAssembly.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表查询 =====
    async def list_with_filters(
        self,
        *,
        customer_id: int | None = None,
        customer_ids_in: list[int] | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        is_urgent: bool | None = None,
        drawing_no_like: str | None = None,
        name_like: str | None = None,
        # 2026-07-31：零件一览并入装配件的补充筛选（与 PartRepository 对齐）
        order_no_like: str | None = None,
        # 2026-07-31：序列号搜索（ILIKE 包含；装配件本身 OR EXISTS 子件匹配）。
        serial_no_like: str | None = None,
        request_date_from: date | None = None,
        request_date_to: date | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        system_delivery_date_from: date | None = None,
        system_delivery_date_to: date | None = None,
        sort_by: AssemblySortKey = AssemblySortKey.PLANNED_DELIVERY_DATE,
        sort_dir: AssemblySortDir = "asc",
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TAssembly]:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            status=status,
            statuses=statuses,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            order_no_like=order_no_like,
            serial_no_like=serial_no_like,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            include_deleted=include_deleted,
        )
        sort_col = {
            AssemblySortKey.PLANNED_DELIVERY_DATE: TAssembly.planned_delivery_date,
            AssemblySortKey.REQUEST_DATE: TAssembly.request_date,
            AssemblySortKey.CREATED_AT: TAssembly.created_at,
            AssemblySortKey.SERIAL_NO: TAssembly.serial_no,
            AssemblySortKey.DRAWING_NO: TAssembly.drawing_no,
            AssemblySortKey.NAME: TAssembly.name,
        }[sort_by]
        if sort_dir == "asc":
            stmt = stmt.order_by(sort_col.asc(), TAssembly.id.desc())
        else:
            stmt = stmt.order_by(sort_col.desc(), TAssembly.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        customer_id: int | None = None,
        customer_ids_in: list[int] | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        is_urgent: bool | None = None,
        drawing_no_like: str | None = None,
        name_like: str | None = None,
        order_no_like: str | None = None,
        # 2026-07-31：序列号搜索（ILIKE 包含；装配件本身 OR EXISTS 子件匹配）。
        serial_no_like: str | None = None,
        request_date_from: date | None = None,
        request_date_to: date | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        system_delivery_date_from: date | None = None,
        system_delivery_date_to: date | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            status=status,
            statuses=statuses,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            order_no_like=order_no_like,
            serial_no_like=serial_no_like,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            include_deleted=include_deleted,
        ).with_only_columns(func.count(TAssembly.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 软删 =====
    async def update(self, assembly: TAssembly) -> TAssembly:
        await self.session.flush()
        return assembly

    async def soft_delete(self, assembly: TAssembly) -> TAssembly:
        assembly.deleted_at = now_naive()
        await self.session.flush()
        return assembly

    # ===== 内部 =====
    def _build_filter_stmt(
        self,
        *,
        customer_id: int | None,
        customer_ids_in: list[int] | None,
        status: str | None,
        statuses: list[str] | None,
        is_urgent: bool | None,
        drawing_no_like: str | None,
        name_like: str | None,
        order_no_like: str | None = None,
        serial_no_like: str | None = None,  # 2026-07-31：序列号（装配件 OR EXISTS 子件匹配）
        request_date_from: date | None = None,
        request_date_to: date | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        system_delivery_date_from: date | None = None,
        system_delivery_date_to: date | None = None,
        include_deleted: bool,
    ):
        stmt = select(TAssembly)
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        if customer_ids_in is not None:
            stmt = stmt.where(TAssembly.customer_id.in_(customer_ids_in))
        elif customer_id is not None:
            stmt = stmt.where(TAssembly.customer_id == customer_id)
        if statuses is not None:
            stmt = stmt.where(TAssembly.status.in_(statuses))
        elif status is not None:
            stmt = stmt.where(TAssembly.status == status)
        if is_urgent is not None:
            stmt = stmt.where(TAssembly.is_urgent.is_(is_urgent))
        if drawing_no_like:
            stmt = stmt.where(
                TAssembly.drawing_no.ilike(f"%{drawing_no_like}%")
            )
        if name_like:
            stmt = stmt.where(TAssembly.name.ilike(f"%{name_like}%"))
        # 2026-07-31：与 PartRepository 对齐——订单号 / 各类日期区间筛选
        if order_no_like:
            on = order_no_like.strip()
            if on:
                stmt = stmt.where(TAssembly.order_no.ilike(f"%{on}%"))
        # 2026-07-31：序列号（装配件 OR EXISTS 子件匹配）。
        # 子件 serial_no 形如 {父装配}-{i:02d}，所以搜子件序列号时，装配件
        # 本身没有匹配的 serial_no —— 需要 EXISTS 命中子件才能带出母装配件行。
        if serial_no_like:
            sn = serial_no_like.strip()
            if sn:
                stmt = stmt.where(
                    or_(
                        TAssembly.serial_no.ilike(f"%{sn}%"),
                        select(TPart.id)
                        .where(
                            (TPart.assembly_id == TAssembly.id)
                            & TPart.serial_no.ilike(f"%{sn}%")
                            & TPart.deleted_at.is_(None)
                        )
                        .exists(),
                    )
                )
        if request_date_from is not None:
            stmt = stmt.where(TAssembly.request_date >= request_date_from)
        if request_date_to is not None:
            stmt = stmt.where(TAssembly.request_date <= request_date_to)
        if planned_delivery_date_from is not None:
            stmt = stmt.where(
                TAssembly.planned_delivery_date >= planned_delivery_date_from
            )
        if planned_delivery_date_to is not None:
            stmt = stmt.where(
                TAssembly.planned_delivery_date <= planned_delivery_date_to
            )
        # system_delivery_date 沿用 PartRepository 的 NULL 兜底：可空字段
        # 在区间内同样命中（PR-F 字段 NULL=未设置）。
        if system_delivery_date_from is not None:
            stmt = stmt.where(
                or_(
                    TAssembly.system_delivery_date.is_(None),
                    TAssembly.system_delivery_date >= system_delivery_date_from,
                )
            )
        if system_delivery_date_to is not None:
            stmt = stmt.where(
                or_(
                    TAssembly.system_delivery_date.is_(None),
                    TAssembly.system_delivery_date <= system_delivery_date_to,
                )
            )
        return stmt