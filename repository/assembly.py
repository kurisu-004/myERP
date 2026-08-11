"""装配件仓储。

照搬 `PartRepository` 形状：构造取 session，方法按 CLAUDE.md §2
约定提供。
"""
from __future__ import annotations

import enum
from datetime import date
from typing import Sequence

from sqlalchemy import and_, func, or_, select
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
    QUANTITY = "QUANTITY"  # 2026-08-01 新增（与 PartSortKey 对齐）
    UNIT_PRICE = "UNIT_PRICE"  # 2026-08-01 新增
    TOTAL_PRICE = "TOTAL_PRICE"  # 2026-08-01 新增


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

    async def list_by_ids(
        self, ids: list[int], *, include_deleted: bool = False
    ) -> list[TAssembly]:
        """按 ID 批查（MCP 到期查询按 assembly_id 聚合子件时防 N+1）。"""
        if not ids:
            return []
        stmt = select(TAssembly).where(TAssembly.id.in_(ids))
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_drawing_no(
        self, drawing_no: str, *, include_deleted: bool = False
    ) -> TAssembly | None:
        stmt = select(TAssembly).where(TAssembly.drawing_no == drawing_no)
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        stmt = stmt.order_by(TAssembly.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 批量图号 / 名称预取（采购订单 Excel 导入匹配用，2026-08-11）=====
    async def list_by_drawing_nos(
        self, codes: Sequence[str], *, include_deleted: bool = False
    ) -> list[TAssembly]:
        """按图号 in_ 批量取装配件；空 codes → 返回空 list。"""
        if not codes:
            return []
        stmt = select(TAssembly).where(TAssembly.drawing_no.in_(list(codes)))
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_names(
        self, names: Sequence[str], *, include_deleted: bool = False
    ) -> list[TAssembly]:
        """按 name in_ 批量取装配件；调用方负责传入归一化后的 name。"""
        if not names:
            return []
        stmt = select(TAssembly).where(TAssembly.name.in_(list(names)))
        if not include_deleted:
            stmt = stmt.where(TAssembly.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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
        # 2026-08-05：图号/名称统一关键词搜索（OR）。修复调用方把同一 keyword
        # 同时传给 drawing_no_like + name_like 导致隐式 AND、装配件几乎搜不出。
        keyword: str | None = None,
        request_date_from: date | None = None,
        request_date_to: date | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        system_delivery_date_from: date | None = None,
        system_delivery_date_to: date | None = None,
        # 2026-08-11 follow-up：可空列空白筛选（与 PartRepository 对齐）。
        # 装配件层之前漏掉这两个开关，导致零件一览勾「仅空白」时非空白装配件仍出现。
        order_no_is_null: bool | None = None,
        system_delivery_date_is_null: bool | None = None,
        # 2026-08-05：装配件本身不带 next_process_id / location / current_holder_id；
        # 这三个参数通过子件 EXISTS 作用于装配件行（C2）。
        child_next_process_ids: list[int] | None = None,
        child_locations: list[str] | None = None,
        child_holder_ids: list[int] | None = None,
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
            keyword=keyword,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            order_no_is_null=order_no_is_null,  # 2026-08-11 follow-up
            system_delivery_date_is_null=system_delivery_date_is_null,  # 2026-08-11 follow-up
            child_next_process_ids=child_next_process_ids,
            child_locations=child_locations,
            child_holder_ids=child_holder_ids,
            include_deleted=include_deleted,
        )
        sort_col = {
            AssemblySortKey.PLANNED_DELIVERY_DATE: TAssembly.planned_delivery_date,
            AssemblySortKey.REQUEST_DATE: TAssembly.request_date,
            AssemblySortKey.CREATED_AT: TAssembly.created_at,
            AssemblySortKey.SERIAL_NO: TAssembly.serial_no,
            AssemblySortKey.DRAWING_NO: TAssembly.drawing_no,
            AssemblySortKey.NAME: TAssembly.name,
            AssemblySortKey.QUANTITY: TAssembly.quantity,  # 2026-08-01 新增
            AssemblySortKey.UNIT_PRICE: TAssembly.unit_price,  # 2026-08-01 新增
            AssemblySortKey.TOTAL_PRICE: TAssembly.total_price,  # 2026-08-01 新增
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
        # 2026-08-05：图号/名称统一关键词搜索（OR）。
        keyword: str | None = None,
        request_date_from: date | None = None,
        request_date_to: date | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        system_delivery_date_from: date | None = None,
        system_delivery_date_to: date | None = None,
        # 2026-08-11 follow-up：可空列空白筛选（与 PartRepository 对齐）。
        order_no_is_null: bool | None = None,
        system_delivery_date_is_null: bool | None = None,
        # 2026-08-05：装配件通过子件 EXISTS 接受 next_process / location / holder 筛选（C2）。
        child_next_process_ids: list[int] | None = None,
        child_locations: list[str] | None = None,
        child_holder_ids: list[int] | None = None,
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
            keyword=keyword,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            order_no_is_null=order_no_is_null,  # 2026-08-11 follow-up
            system_delivery_date_is_null=system_delivery_date_is_null,  # 2026-08-11 follow-up
            child_next_process_ids=child_next_process_ids,
            child_locations=child_locations,
            child_holder_ids=child_holder_ids,
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
        # 2026-08-05：图号/名称统一关键词搜索（OR）。
        keyword: str | None = None,
        request_date_from: date | None = None,
        request_date_to: date | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
        system_delivery_date_from: date | None = None,
        system_delivery_date_to: date | None = None,
        # 2026-08-11 follow-up：可空列空白筛选（与 PartRepository 对齐）。
        order_no_is_null: bool | None = None,
        system_delivery_date_is_null: bool | None = None,
        # 2026-08-05：装配件本身不带 next_process_id / location / current_holder_id。
        # 通过子件 EXISTS 命中装配件；与 PartRepository 语义一致：
        # child_locations / child_holder_ids 之间 OR，二者皆空时不加 OR 段；
        # child_next_process_ids 与 (loc OR holder) 之间 AND；皆空时整段 EXISTS 不加。
        child_next_process_ids: list[int] | None = None,
        child_locations: list[str] | None = None,
        child_holder_ids: list[int] | None = None,
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
        # 2026-08-05：图号/名称统一关键词搜索（OR）。此前调用方把同一 keyword
        # 同时传给 drawing_no_like + name_like 导致隐式 AND，装配件几乎搜不出来。
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TAssembly.drawing_no.ilike(f"%{kw}%")
                    | TAssembly.name.ilike(f"%{kw}%")
                )
        # 2026-07-31：与 PartRepository 对齐——订单号 / 各类日期区间筛选
        # 2026-08-11 follow-up：与 order_no_is_null 互斥——is_null 显式设值时覆盖 order_no_like。
        if order_no_like and order_no_is_null is None:
            on = order_no_like.strip()
            if on:
                stmt = stmt.where(TAssembly.order_no.ilike(f"%{on}%"))
        # 2026-08-11 follow-up：订单号空白筛选（覆盖 order_no_like ILIKE）。
        if order_no_is_null is True:
            # "空白" = IS NULL OR == ''；空串与 NULL 共存于 order_no String(30) 列。
            stmt = stmt.where(or_(TAssembly.order_no.is_(None), TAssembly.order_no == ""))
        elif order_no_is_null is False:
            stmt = stmt.where(and_(TAssembly.order_no.is_not(None), TAssembly.order_no != ""))
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
        # system_delivery_date 沿用 PartRepository 的 NULL 语义（2026-08-11 修复）：
        # 区间条件不再 NULL 兜底，NULL 与日期比较返回 NULL → 不命中（标准 SQL）。
        # 与 PartRepository._build_filter_stmt 保持一致，避免零件行与装配件行行为发散。
        # 2026-08-11 follow-up：与 system_delivery_date_is_null 互斥——is_null=True 时区间失效。
        if system_delivery_date_is_null is True:
            stmt = stmt.where(TAssembly.system_delivery_date.is_(None))
        else:
            if system_delivery_date_from is not None:
                stmt = stmt.where(
                    TAssembly.system_delivery_date >= system_delivery_date_from
                )
            if system_delivery_date_to is not None:
                stmt = stmt.where(
                    TAssembly.system_delivery_date <= system_delivery_date_to
                )
            # False ⇒ 仅非 NULL（区间条件照常生效，仍排除 NULL）。
            if system_delivery_date_is_null is False:
                stmt = stmt.where(TAssembly.system_delivery_date.is_not(None))
        # 2026-08-05：子件 EXISTS 形态的「下一道工序 / 物理位置 / holder」筛选（C2）。
        # 三个参数独立判断；任意一个非空时整体加 EXISTS 子查询。
        if child_next_process_ids or child_locations or child_holder_ids:
            from sqlalchemy import bindparam

            child_conds = [TPart.assembly_id == TAssembly.id, TPart.deleted_at.is_(None)]
            if child_next_process_ids:
                child_conds.append(
                    TPart.next_process_id.in_(
                        bindparam("child_next_process_ids", expanding=True)
                    )
                )
            _loc_terms: list = []
            if child_locations:
                _loc_terms.append(
                    TPart.location.in_(
                        bindparam("child_locations", expanding=True)
                    )
                )
            if child_holder_ids:
                _loc_terms.append(
                    TPart.current_holder_id.in_(
                        bindparam("child_holder_ids", expanding=True)
                    )
                )
            if len(_loc_terms) == 1:
                child_conds.append(_loc_terms[0])
            elif len(_loc_terms) > 1:
                child_conds.append(or_(*_loc_terms))
            stmt = stmt.where(
                select(TPart.id).where(*child_conds).exists()
            )
            params: dict = {}
            if child_next_process_ids:
                params["child_next_process_ids"] = list(child_next_process_ids)
            if child_locations:
                params["child_locations"] = list(child_locations)
            if child_holder_ids:
                params["child_holder_ids"] = list(child_holder_ids)
            stmt = stmt.params(**params)
        return stmt