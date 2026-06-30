"""装配件仓储。

照搬 `PartRepository` 形状：构造取 session，方法按 CLAUDE.md §2
约定提供。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TAssembly


AssemblySortKey = Literal["planned_delivery_date", "request_date", "created_at"]
AssemblySortDir = Literal["asc", "desc"]


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
        status: str | None = None,
        is_urgent: bool | None = None,
        drawing_no_like: str | None = None,
        name_like: str | None = None,
        sort_by: AssemblySortKey = "planned_delivery_date",
        sort_dir: AssemblySortDir = "asc",
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TAssembly]:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            status=status,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            include_deleted=include_deleted,
        )
        sort_col = {
            "planned_delivery_date": TAssembly.planned_delivery_date,
            "request_date": TAssembly.request_date,
            "created_at": TAssembly.created_at,
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
        status: str | None = None,
        is_urgent: bool | None = None,
        drawing_no_like: str | None = None,
        name_like: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            status=status,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            include_deleted=include_deleted,
        ).with_only_columns(func.count(TAssembly.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 软删 =====
    async def update(self, assembly: TAssembly) -> TAssembly:
        await self.session.flush()
        return assembly

    async def soft_delete(self, assembly: TAssembly) -> TAssembly:
        assembly.deleted_at = datetime.utcnow()
        await self.session.flush()
        return assembly

    # ===== 内部 =====
    def _build_filter_stmt(
        self,
        *,
        customer_id: int | None,
        status: str | None,
        is_urgent: bool | None,
        drawing_no_like: str | None,
        name_like: str | None,
        include_deleted: bool,
    ):
        stmt = select(TAssembly)
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        if customer_id is not None:
            stmt = stmt.where(TAssembly.customer_id == customer_id)
        if status is not None:
            stmt = stmt.where(TAssembly.status == status)
        if is_urgent is not None:
            stmt = stmt.where(TAssembly.is_urgent.is_(is_urgent))
        if drawing_no_like:
            stmt = stmt.where(
                TAssembly.drawing_no.ilike(f"%{drawing_no_like}%")
            )
        if name_like:
            stmt = stmt.where(TAssembly.name.ilike(f"%{name_like}%"))
        return stmt