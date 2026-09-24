from datetime import datetime
from typing import Sequence

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TPart, TPartBatch
from model.enums import PartSortKey, PartStatus, SortDir


def _chain_step_process_subq():
    """2026-09-16 PR-3：t_part_batch.next_process_id 列已删（Rust 迁移 028），
    改用 ``current_process_step_id → t_process_chain_step.process_id`` 派生
    工序筛选条件。本函数返回 correlated scalar subquery，对每行 batch 取
    其当前 step 的 process_id（未软删 step），NULL 表示批次尚未进入生产流
    或所属 step 已软删。在 ``WHERE ... .in_(process_ids)`` 里替换原
    ``TPartBatch.next_process_id.in_(process_ids)`` 即可。
    """
    return (
        select(TProcessChainStep.process_id)
        .where(
            TProcessChainStep.id == TPartBatch.current_process_step_id,
            TProcessChainStep.deleted_at.is_(None),
        )
        .correlate(TPartBatch)
        .scalar_subquery()
    )


class PartRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, part: TPart) -> TPart:
        self.session.add(part)
        await self.session.flush()
        return part

    async def create_many(self, parts: list[TPart]) -> list[TPart]:
        self.session.add_all(parts)
        await self.session.flush()
        return parts

    # ===== 单条查询 =====
    async def get_by_id(
        self, part_id: int, *, include_deleted: bool = False
    ) -> TPart | None:
        part = await self.session.get(TPart, part_id)
        if part is None:
            return None
        if not include_deleted and part.deleted_at is not None:
            return None
        return part

    async def get_by_drawing_no(
        self, drawing_no: str, *, include_deleted: bool = False
    ) -> TPart | None:
        """按图号精确匹配（车间扫码定位用）。返回最近一条未软删记录。"""
        stmt = select(TPart).where(TPart.drawing_no == drawing_no)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        stmt = stmt.order_by(TPart.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_ids(
        self,
        part_ids: list[int],
        *,
        include_deleted: bool = False,
        order: str = "id",
    ) -> list[TPart]:
        """按 ID 批查（送货单生成专用，避免 N+1）。

        - 默认按 id ASC（与 caller 传入顺序一致，方便 caller 直接对齐）
        - include_deleted=False 时过滤掉软删件；caller 拿到 None 表示缺失。
        - 空列表短路返 []，不触发 DB。
        """
        if not part_ids:
            return []
        stmt = select(TPart).where(TPart.id.in_(part_ids))
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        if order == "id":
            stmt = stmt.order_by(TPart.id.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表查询（核心：前缀搜索 + 多维过滤 + 排序） =====
    async def list_with_filters(
        self,
        *,
        customer_id: int | None = None,
        customer_ids_in: list[int] | None = None,
        statuses: list[PartStatus] | None = None,
        is_urgent: bool | None = None,
        # 2026-08-20：drawing_no / name 替代 keyword 在 /parts 列表的用法；
        # keyword 保留作兼容其他端点（outsource picker 等）的 fallback。
        drawing_no: str | None = None,
        name: str | None = None,
        keyword: str | None = None,
        order_no: str | None = None,
        serial_no: str | None = None,  # 2026-07-31：序列号独立搜索（ILIKE 包含）
        has_outsource_history: bool | None = None,
        request_date_from=None,
        request_date_to=None,
        planned_delivery_date_from=None,
        planned_delivery_date_to=None,
        system_delivery_date_from=None,
        system_delivery_date_to=None,
        # 2026-08-11：订单号空白筛选。None=任意 / True=仅空白(NULL OR '') / False=仅非空。
        order_no_is_null: bool | None = None,
        # 2026-08-11：系统交期空白筛选。None=区间默认排除NULL / True=仅NULL(区间失效) / False=区间+仅非空。
        system_delivery_date_is_null: bool | None = None,
        # 2026-08-08：True 时额外要求 system_delivery_date IS NOT NULL（MCP 到期查询用）。
        # 2026-08-11：Bug 1 修复后区间条件已默认排除 NULL；本参数叠加作为防御性冗余保留。
        system_delivery_date_not_null: bool | None = None,
        next_process_ids: list[int] | None = None,  # 2026-08-01：下一道工序多选
        # 2026-09-16 删除 `locations` / `holder_ids` 两参（t_part 瘦身，
        # Rust 迁移 027）：其 WHERE 依赖已删的 t_part.location / current_holder_id
        # 列；唯一调用方是 dormant 的 v1 /parts 列表。位置/holder 过滤请走批次。
        sort_by: PartSortKey = PartSortKey.PLANNED_DELIVERY_DATE,
        sort_dir: SortDir = SortDir.ASC,
        include_deleted: bool = False,
        assembly_id_is_null: bool | None = None,
        # 2026-08-05：把结果收敛到指定装配件的子件集合（C2 命中子件回显用）。
        # 与 `assembly_id_is_null` 互斥：本参数显式指定子件所属装配件 id 集合；
        # 调用方传本参数时不要再设 assembly_id_is_null。
        assembly_ids_in: list[int] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TPart]:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            statuses=statuses,
            is_urgent=is_urgent,
            drawing_no=drawing_no,
            name=name,
            keyword=keyword,
            order_no=order_no,
            serial_no=serial_no,
            has_outsource_history=has_outsource_history,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            order_no_is_null=order_no_is_null,  # 2026-08-11
            system_delivery_date_is_null=system_delivery_date_is_null,  # 2026-08-11
            system_delivery_date_not_null=system_delivery_date_not_null,
            next_process_ids=next_process_ids,
            include_deleted=include_deleted,
            assembly_id_is_null=assembly_id_is_null,
            assembly_ids_in=assembly_ids_in,
        )
        sort_col = {
            PartSortKey.PLANNED_DELIVERY_DATE: TPart.planned_delivery_date,
            PartSortKey.REQUEST_DATE: TPart.request_date,
            PartSortKey.SYSTEM_DELIVERY_DATE: TPart.system_delivery_date,
            PartSortKey.CREATED_AT: TPart.created_at,
            PartSortKey.SERIAL_NO: TPart.serial_no,
            PartSortKey.DRAWING_NO: TPart.drawing_no,
            PartSortKey.NAME: TPart.name,
            PartSortKey.ORDER_NO: TPart.order_no,
            PartSortKey.QUANTITY: TPart.quantity,  # 2026-08-01 新增
            PartSortKey.UNIT_PRICE: TPart.unit_price,  # 2026-08-01 新增
            PartSortKey.TOTAL_PRICE: TPart.total_price,  # 2026-08-01 新增
        }[sort_by]
        # 2026-07-21：可空列（system_delivery_date / order_no）排序时 NULL 排末尾。
        _nulls_last_keys = {
            PartSortKey.SYSTEM_DELIVERY_DATE,
            PartSortKey.ORDER_NO,
        }
        if sort_dir == SortDir.ASC:
            if sort_by in _nulls_last_keys:
                stmt = stmt.order_by(sort_col.asc().nulls_last(), TPart.id.desc())
            else:
                stmt = stmt.order_by(sort_col.asc(), TPart.id.desc())
        else:
            if sort_by in _nulls_last_keys:
                stmt = stmt.order_by(sort_col.desc().nulls_last(), TPart.id.desc())
            else:
                stmt = stmt.order_by(sort_col.desc(), TPart.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        customer_id: int | None = None,
        customer_ids_in: list[int] | None = None,
        statuses: list[PartStatus] | None = None,
        is_urgent: bool | None = None,
        # 2026-08-20：drawing_no / name 替代 keyword 在 /parts 列表的用法；
        # keyword 保留作兼容其他端点（outsource picker 等）的 fallback。
        drawing_no: str | None = None,
        name: str | None = None,
        keyword: str | None = None,
        order_no: str | None = None,
        serial_no: str | None = None,  # 2026-07-31：序列号独立搜索（ILIKE 包含）
        has_outsource_history: bool | None = None,
        request_date_from=None,
        request_date_to=None,
        planned_delivery_date_from=None,
        planned_delivery_date_to=None,
        system_delivery_date_from=None,
        system_delivery_date_to=None,
        # 2026-08-11：订单号空白筛选。None=任意 / True=仅空白(NULL OR '') / False=仅非空。
        order_no_is_null: bool | None = None,
        # 2026-08-11：系统交期空白筛选。None=区间默认排除NULL / True=仅NULL(区间失效) / False=区间+仅非空。
        system_delivery_date_is_null: bool | None = None,
        # 2026-08-08：True 时额外要求 system_delivery_date IS NOT NULL（MCP 到期查询用）。
        # 2026-08-11：Bug 1 修复后区间条件已默认排除 NULL；本参数叠加作为防御性冗余保留。
        system_delivery_date_not_null: bool | None = None,
        next_process_ids: list[int] | None = None,  # 2026-08-01：下一道工序多选
        # 2026-09-16 删除 `locations` / `holder_ids` 两参（t_part 瘦身，
        # Rust 迁移 027）：其 WHERE 依赖已删的 t_part.location / current_holder_id
        # 列；唯一调用方是 dormant 的 v1 /parts 列表。位置/holder 过滤请走批次。
        include_deleted: bool = False,
        assembly_id_is_null: bool | None = None,
        # 2026-08-05：把 count 收敛到指定装配件的子件集合（C2 命中子件回显用）。
        assembly_ids_in: list[int] | None = None,
    ) -> int:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            statuses=statuses,
            is_urgent=is_urgent,
            drawing_no=drawing_no,
            name=name,
            keyword=keyword,
            order_no=order_no,
            serial_no=serial_no,
            has_outsource_history=has_outsource_history,
            request_date_from=request_date_from,
            request_date_to=request_date_to,
            planned_delivery_date_from=planned_delivery_date_from,
            planned_delivery_date_to=planned_delivery_date_to,
            system_delivery_date_from=system_delivery_date_from,
            system_delivery_date_to=system_delivery_date_to,
            order_no_is_null=order_no_is_null,  # 2026-08-11
            system_delivery_date_is_null=system_delivery_date_is_null,  # 2026-08-11
            system_delivery_date_not_null=system_delivery_date_not_null,
            next_process_ids=next_process_ids,
            include_deleted=include_deleted,
            assembly_id_is_null=assembly_id_is_null,
            assembly_ids_in=assembly_ids_in,
        ).with_only_columns(func.count(TPart.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 删除（软删） =====
    async def update(self, part: TPart) -> TPart:
        await self.session.flush()
        return part

    async def soft_delete(self, part: TPart) -> TPart:
        part.deleted_at = now_naive()
        await self.session.flush()
        return part

    # ===== 序列号查询 =====
    async def get_by_serial(
        self, serial_no: str, *, include_deleted: bool = False
    ) -> TPart | None:
        stmt = select(TPart).where(TPart.serial_no == serial_no)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 装配体关联 =====
    async def list_children(self, assembly_id: int) -> list[TPart]:
        """取装配件的所有子零件，按 drawing_no 升序（保证 PDF 页顺序）。"""
        stmt = (
            select(TPart)
            .where(
                TPart.assembly_id == assembly_id,
                TPart.deleted_at.is_(None),
            )
            .order_by(TPart.drawing_no.asc(), TPart.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 批量图号 / 名称预取（采购订单 Excel 导入匹配用，2026-08-11）=====
    async def list_by_drawing_nos(
        self, codes: Sequence[str], *, include_deleted: bool = False
    ) -> list[TPart]:
        """按图号 in_ 批量取零件；用于采购订单 Excel 匹配阶段的零 N+1 预取。

        返回全部匹配记录（含同名图号的多个零件），调用方按需聚合。
        空 codes → 返回空 list（短路，避免无意义的全表扫描）。
        """
        if not codes:
            return []
        stmt = select(TPart).where(TPart.drawing_no.in_(list(codes)))
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_names(
        self, names: Sequence[str], *, include_deleted: bool = False
    ) -> list[TPart]:
        """按 name in_ 批量取零件；用于名称归一化后的等值 in_ 匹配。

        调用方负责传入归一化后的 name（去空白、全角→半角、小写）；
        仓储层只做 in_ 查询，避免全表 ilike 扫描。
        """
        if not names:
            return []
        stmt = select(TPart).where(TPart.name.in_(list(names)))
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 工人持有件 / 工种取件列表 =====
    # 2026-09-16 删除（t_part 瘦身，Rust 迁移 027）：原 `list_held_by_worker` /
    # `list_for_work_type` / `list_for_work_type_all_shelves` 三个扫码台查询的
    # WHERE 全部依赖已删的 t_part.location / current_holder_id 列。v1 扫码端点
    # 已 dormant（2026-09-15 Phase 5 起前端走 v2），且 service 层实际调用的是
    # `repository/part_batch.py` 的同名批次版方法（t_part_batch 列保留），
    # 本类这三个方法早已无调用方，随删列一并移除，不再保留炸弹。

    # ===== 申请人引用计数（软删前 BIZ_APPLICANT_IN_USE 校验）=====
    async def count_by_applicant_name_in_customers(
        self,
        applicant_name: str,
        customer_ids: list[int],
        *,
        include_deleted: bool = False,
    ) -> int:
        """统计未软删零件中 `applicant_name` 等于指定值、`customer_id` 在
        传入的客户 id 集合内的记录数。`customer_ids` 应包含一级客户及其所有
        二级子节点 id（由 ApplicantService.soft_delete_applicant 拼好后传入）。
        """
        if not customer_ids:
            return 0
        stmt = select(func.count(TPart.id)).where(
            TPart.applicant_name == applicant_name,
            TPart.customer_id.in_(customer_ids),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 7 天自动完成（PR-D 2026-07-10）=====
    async def find_delivered_older_than(
        self,
        *,
        threshold: datetime,
        limit: int = 200,
    ) -> list[TPart]:
        """查找 DELIVERED 状态且"最近一次发货事件"早于 threshold 的零件。

        SQL 谓词（防"返修干扰"用 NOT EXISTS）：
        - status = 'DELIVERED' AND deleted_at IS NULL
        - (SELECT MAX(e.created_at) FROM t_part_event e
             WHERE e.part_id = p.id
               AND e.event_type = 'STATUS_CHANGED'
               AND e.from_status = 'READY_TO_SHIP'
               AND e.to_status = 'DELIVERED') <= threshold
        - NOT EXISTS (
            SELECT 1 FROM t_part_event e
            WHERE e.part_id = p.id
              AND e.event_type = 'REPAIR_STARTED'
              AND e.created_at > (
                  SELECT MAX(e2.created_at) FROM t_part_event e2
                  WHERE e2.part_id = p.id
                    AND e2.event_type = 'STATUS_CHANGED'
                    AND e2.from_status = 'READY_TO_SHIP'
                    AND e2.to_status = 'DELIVERED'
              )
          )

        排序：id ASC（保持稳定）；limit 上限防内存爆。
        """
        # 关键：DELIVERED 事件用 from/to_status 复合（state machine 写的 STATUS_CHANGED）
        delivered_event_filter = and_(
            TPartEvent.part_id == TPart.id,
            TPartEvent.event_type == PartEventType.STATUS_CHANGED.value,
            TPartEvent.from_status == PartStatus.READY_TO_SHIP.value,
            TPartEvent.to_status == PartStatus.DELIVERED.value,
        )
        latest_delivered = (
            select(func.max(TPartEvent.created_at))
            .where(delivered_event_filter)
            .correlate(TPart)
            .scalar_subquery()
        )
        # 返修干扰：是否有 REPAIR_STARTED 在最近一次 DELIVERED 之后
        repair_after_delivered = (
            select(TPartEvent.id)
            .where(
                TPartEvent.part_id == TPart.id,
                TPartEvent.event_type == PartEventType.REPAIR_STARTED.value,
                TPartEvent.created_at > latest_delivered,
            )
            .correlate(TPart)
            .exists()
        )
        stmt = (
            select(TPart)
            .where(
                TPart.status == PartStatus.DELIVERED.value,
                TPart.deleted_at.is_(None),
                latest_delivered <= threshold,
                ~repair_after_delivered,
            )
            .order_by(TPart.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 在架件数批量统计 =====
    # 2026-09-16 删除（t_part 瘦身，Rust 迁移 027）：原 `get_load_map_by_shelf_ids`
    # 按 t_part.current_holder_id / location 统计在架件数，两列已删；
    # 唯一调用方 service/shelf.py 的 v1 货架卡片已 dormant，一并移除不再保留。

    # ===== 内部 =====
    def _build_filter_stmt(
        self,
        *,
        customer_id: int | None,
        customer_ids_in: list[int] | None,
        statuses: list[PartStatus] | None,
        is_urgent: bool | None,
        # 2026-08-20：drawing_no / name 替代 keyword 在 /parts 列表的用法；
        # keyword 保留作兼容其他端点（outsource picker 等）的 fallback。
        drawing_no: str | None = None,
        name: str | None = None,
        keyword: str | None = None,
        order_no: str | None = None,
        serial_no: str | None = None,  # 2026-07-31：序列号独立搜索（ILIKE 包含）
        has_outsource_history: bool | None,
        request_date_from=None,
        request_date_to=None,
        planned_delivery_date_from=None,
        planned_delivery_date_to=None,
        system_delivery_date_from=None,
        system_delivery_date_to=None,
        # 2026-08-11：订单号空白筛选。None=任意 / True=仅空白(NULL OR '') / False=仅非空。
        order_no_is_null: bool | None = None,
        # 2026-08-11：系统交期空白筛选。None=区间默认排除NULL / True=仅NULL(区间失效) / False=区间+仅非空。
        system_delivery_date_is_null: bool | None = None,
        # 2026-08-08：True 时额外要求 system_delivery_date IS NOT NULL（MCP 到期查询用）。
        # 2026-08-11：Bug 1 修复后区间条件已默认排除 NULL；本参数叠加作为防御性冗余保留。
        system_delivery_date_not_null: bool | None = None,
        next_process_ids: list[int] | None = None,  # 2026-08-01：下一道工序多选
        # 2026-09-16 删除 `locations` / `holder_ids` 两参（t_part 瘦身，
        # Rust 迁移 027）：其 WHERE 依赖已删的 t_part.location / current_holder_id
        # 列；唯一调用方是 dormant 的 v1 /parts 列表。位置/holder 过滤请走批次。
        include_deleted: bool,
        assembly_id_is_null: bool | None = None,
        # 2026-08-05：把结果收敛到指定装配件的子件集合（C2 命中子件回显用）。
        assembly_ids_in: list[int] | None = None,
    ):
        stmt = select(TPart)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        # 客户筛选二选一：customer_ids_in 优先（级联：L1 含其下 L2）。
        # 调用方有且只能传其中一个。
        if customer_ids_in is not None:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        elif customer_id is not None:
            stmt = stmt.where(TPart.customer_id == customer_id)
        if statuses:
            stmt = stmt.where(
                TPart.status.in_([s.value for s in statuses])
            )
        if is_urgent is not None:
            stmt = stmt.where(TPart.is_urgent.is_(is_urgent))
        # 2026-08-20：图号 / 名称拆为两个独立 ILIKE 子串参数（替换原 keyword 在 /parts 列表的用法）。
        # 两个参数同时设 ⇒ AND 联合（drawing_no ILIKE AND name ILIKE）。
        # TODO: 后续把 %/_ 通配符转义（参考 repository/applicant.py:131-133）
        if drawing_no:
            dw = drawing_no.strip()
            if dw:
                stmt = stmt.where(TPart.drawing_no.ilike(f"%{dw}%"))
        if name:
            n = name.strip()
            if n:
                stmt = stmt.where(TPart.name.ilike(f"%{n}%"))
        # 2026-08-20：keyword 保留为兼容其他端点（pending-programming / outsource picker 等），
        # /parts 主路径不再使用。如果同时设 drawing_no/name + keyword，三个块都生效（AND 联合）。
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"%{kw}%")
                )
        # 2026-07-22：订单号独立搜索框（ILIKE 子串包含）。
        # 2026-08-11：与 order_no_is_null 互斥——is_null 显式设值时覆盖 order_no ILIKE。
        if order_no and order_no_is_null is None:
            on = order_no.strip()
            if on:
                stmt = stmt.where(TPart.order_no.ilike(f"%{on}%"))
        # 2026-08-11：订单号空白筛选。覆盖 order_no 子串搜索：
        # True  ⇒ 仅空白（NULL OR ''）；order_no 文本被忽略。
        # False ⇒ 仅非空（NULL AND != '' 排除）；order_no 文本被忽略。
        # None  ⇒ 不加条件（沿用 order_no ILIKE 子串搜索或不限）。
        if order_no_is_null is True:
            stmt = stmt.where(or_(TPart.order_no.is_(None), TPart.order_no == ""))
        elif order_no_is_null is False:
            stmt = stmt.where(and_(TPart.order_no.is_not(None), TPart.order_no != ""))
        # 2026-07-31：序列号独立搜索框（ILIKE 子串包含）。
        # 命中子件也算命中（子件 serial_no 形如 {父装配}-{i:02d}）。
        if serial_no:
            sn = serial_no.strip()
            if sn:
                stmt = stmt.where(TPart.serial_no.ilike(f"%{sn}%"))
        # 2026-07-21：PR-F 日期区间筛选（请购日期 / 系统交期）。
        # 仅端点非 None 时加条件；端点为 None 表示半开区间。
        # 系统交期可空（PR-F 字段 NULL=未设置），2026-08-11 起区间条件不再 NULL 兜底——
        # 标准 SQL 语义：NULL 与任何日期比较都返回 NULL → 不命中。需包含 NULL 时调用方
        # 单独传 `system_delivery_date_is_null=True`。用 BETWEEN（含端点）；如只要单向
        # < 或 >，传 None 即可。
        if request_date_from is not None:
            stmt = stmt.where(TPart.request_date >= request_date_from)
        if request_date_to is not None:
            stmt = stmt.where(TPart.request_date <= request_date_to)
        # 2026-07-22：计划交期区间（planned_delivery_date NOT NULL，无需 NULL 兜底）。
        if planned_delivery_date_from is not None:
            stmt = stmt.where(TPart.planned_delivery_date >= planned_delivery_date_from)
        if planned_delivery_date_to is not None:
            stmt = stmt.where(TPart.planned_delivery_date <= planned_delivery_date_to)
        # 2026-08-11 Bug 1 修复：区间条件不再用 or_(is_(None), >= / <=) 包裹；
        # NULL 的 system_delivery_date 因 SQL 比较返回 NULL 而被自然排除。
        # 2026-08-11：与 system_delivery_date_is_null 互斥——is_null=True 时区间失效。
        if system_delivery_date_is_null is True:
            # 区间条件失效，仅返回 NULL。
            stmt = stmt.where(TPart.system_delivery_date.is_(None))
        else:
            if system_delivery_date_from is not None:
                stmt = stmt.where(TPart.system_delivery_date >= system_delivery_date_from)
            if system_delivery_date_to is not None:
                stmt = stmt.where(TPart.system_delivery_date <= system_delivery_date_to)
            # False ⇒ 仅非 NULL（区间条件照常生效，仍排除 NULL）。
            if system_delivery_date_is_null is False:
                stmt = stmt.where(TPart.system_delivery_date.is_not(None))
        # 2026-08-08：MCP 只读查询用。Bug 1 修复后区间条件已默认排除 NULL；本参数叠加
        # 仅作为防御性冗余保留，行为不变（`True` ⇒ `IS NOT NULL`，与区间条件 AND）。
        # 未来可在确认所有 MCP 调用方迁移到 `system_delivery_date_is_null=False` 后移除。
        if system_delivery_date_not_null:
            stmt = stmt.where(TPart.system_delivery_date.is_not(None))
        # 2026-07-20：外协接收历史页（曾外协过判定）与列表 SQL 合一。
        # EXISTS 子查询命中 `t_part_event` 复合索引 `(part_id, created_at)`，
        # list + count 共用同一谓词，行为完全对齐。
        if has_outsource_history:
            stmt = stmt.where(
                select(TPartEvent.part_id)
                .where(
                    TPartEvent.part_id == TPart.id,
                    or_(
                        TPartEvent.event_type.in_([
                            PartEventType.SENT_TO_OUTSOURCE.value,
                            PartEventType.RECEIVED_FROM_OUTSOURCE.value,
                        ]),
                        and_(
                            TPartEvent.event_type == PartEventType.INSPECTED.value,
                            TPartEvent.note.ilike("%外协%"),
                        ),
                    ),
                )
                .exists()
            )
        # 2026-07-30：装配体并入零件一览——排除装配件子件
        if assembly_id_is_null:
            stmt = stmt.where(TPart.assembly_id.is_(None))
        # 2026-08-05：把结果收敛到指定装配件的子件集合（C2 命中子件回显用）。
        # 与 `assembly_id_is_null` 互斥：调用方二选一。
        if assembly_ids_in:
            from sqlalchemy import bindparam

            stmt = stmt.where(
                TPart.assembly_id.in_(
                    bindparam("assembly_ids_in", expanding=True)
                )
            )
            stmt = stmt.params(assembly_ids_in=list(assembly_ids_in))
        # 2026-08-01：下一道工序 / 物理位置多选筛选。
        # 与 `statuses` 语义一致：None / 空列表 = 无筛选；非空列表走 IN 谓词。
        # `next_process_id IS NULL` 的零件会被 SQL `IN` 排除，符合「未指派下一道工序 = 不参与筛选」。
        if next_process_ids:
            stmt = stmt.where(TPart.next_process_id.in_(next_process_ids))
        # 2026-09-16 删除 locations / holder_ids 过滤分支（t_part 瘦身，
        # Rust 迁移 027）：t_part.location / current_holder_id 列已删。
        return stmt

    # ============================================================
    # 外协列表专用（2026-07-16 新增；2026-07-29 批次化）
    # ============================================================
    def _build_outsource_sendable_stmt(
        self,
        *,
        part_ids_in: list[int] | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        is_urgent: bool | None = None,
        include_deleted: bool = False,
    ):
        """外协发送一览的共享 statement builder（list / count 复用，避免谓词漂移）。

        资格条件（2026-07-29 批次化 PR-fix-0.2.0）：
        - TPartBatch.status='IN_PROCESS' AND TPartBatch.location='PRODUCTION_SHELF'
        - AND TPartBatch.current_holder_id IN (绑定了 OUTSOURCE 工序的货架 id 集合)
        - TPartBatch.deleted_at IS NULL

        旧版用 TPart.* 过滤，但 TPart.status/location/current_holder_id 是 rollup 派生自
        「最落后」活跃批次；多批次工单里可外发批次会被 rollup 过滤掉。本方法改成走批次，
        每个可外发批次独立成行。

        `part_ids_in` 用于把候选收敛到「有 APPROVED 报价」的零件集合（外协发送页专用）。
        空列表由调用方短路，不进这里。
        """
        from model import TProcess as _TProc
        from model import TShelfProcess as _TSP
        from model.enums import ProcessCategory as _PC

        outsource_shelf_ids_subq = (
            select(distinct(_TSP.shelf_id))
            .join(_TProc, _TProc.id == _TSP.process_id)
            .where(_TProc.deleted_at.is_(None))
            .where(_TSP.deleted_at.is_(None))
            .where(_TProc.category == _PC.OUTSOURCE.value)
        )
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        stmt = stmt.where(
            TPartBatch.status == "IN_PROCESS",
            TPartBatch.location == "PRODUCTION_SHELF",
            TPartBatch.current_holder_id.in_(outsource_shelf_ids_subq),
        )
        if part_ids_in is not None:
            stmt = stmt.where(TPart.id.in_(part_ids_in))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if is_urgent is not None:
            stmt = stmt.where(TPart.is_urgent.is_(is_urgent))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"{kw}%")
                )
        return stmt

    async def list_outsource_sendable(
        self,
        *,
        part_ids_in: list[int] | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        is_urgent: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[tuple[TPartBatch, TPart]]:
        """外协发送一览：可发送外协的零件（一页）。行=批次（2026-07-29 批次化）。

        排序：is_urgent DESC, planned_delivery_date ASC, id DESC（工单级排序，批次次序稳定）。
        `part_ids_in == []` 由调用方短路（此处不特判，空列表 IN() 会返回 0 行）。

        返回 (TPartBatch, TPart) 元组列表；service 层用批次字段（status/location/holder/
        version/quantity/next_process_id）拼 OutsourceSendableItem。
        """
        stmt = self._build_outsource_sendable_stmt(
            part_ids_in=part_ids_in,
            customer_ids_in=customer_ids_in,
            keyword=keyword,
            is_urgent=is_urgent,
            include_deleted=include_deleted,
        )
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        # TPart → TPartBatch 同时 select，需要 unique() 去重 TPart
        return [(b, p) for p, b in result.unique().all()]

    async def count_outsource_sendable(
        self,
        *,
        part_ids_in: list[int] | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        is_urgent: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        """外协发送一览的总数（与 list_outsource_sendable 同谓词，按批次计）。"""
        stmt = self._build_outsource_sendable_stmt(
            part_ids_in=part_ids_in,
            customer_ids_in=customer_ids_in,
            keyword=keyword,
            is_urgent=is_urgent,
            include_deleted=include_deleted,
        ).with_only_columns(func.count(TPartBatch.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ============================================================
    # 直接发送外协候选（2026-07-28 新增）
    # ============================================================
    async def list_direct_outsource_candidates(
        self,
        *,
        c2_shelf_id: int,
        process_ids: list[int],
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[tuple[TPartBatch, TPart]]:
        """直接发送外协候选（next_process.requires_approval=false 且位于 C2 货架）。行=批次。

        谓词（2026-07-29 批次化；与 list_outsource_sendable 区别）：
        - TPartBatch.status='IN_PROCESS' + TPartBatch.location='PRODUCTION_SHELF'
        - TPartBatch.current_holder_id = c2_shelf_id（必须在 C2 货架上）
        - TPartBatch.next_process_id IN process_ids（上游 service 已筛选 requires_approval=false 的 OUTSOURCE 工序）

排序：is_urgent DESC, planned_delivery_date ASC, id DESC
        """
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPartBatch.status == "IN_PROCESS",
            TPartBatch.location == "PRODUCTION_SHELF",
            TPartBatch.current_holder_id == c2_shelf_id,
            # 2026-09-16 PR-3：next_process_id 列已删，改为派生 step.process_id。
            _chain_step_process_subq().in_(process_ids),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"%{kw}%")
                )
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [(b, p) for p, b in result.unique().all()]

    async def count_direct_outsource_candidates(
        self,
        *,
        c2_shelf_id: int,
        process_ids: list[int],
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        """直接发送外协候选总数（按批次计；与 list_direct_outsource_candidates 同谓词）。"""
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPartBatch.status == "IN_PROCESS",
            TPartBatch.location == "PRODUCTION_SHELF",
            TPartBatch.current_holder_id == c2_shelf_id,
            # 2026-09-16 PR-3：next_process_id 列已删，改为派生 step.process_id。
            _chain_step_process_subq().in_(process_ids),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"%{kw}%")
                )
        stmt = stmt.with_only_columns(func.count(TPartBatch.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())


    # ============================================================
    # 统一外协可发送一览查询（2026-07-28 新增，取代旧的 sendable / direct_outsource）
    # ============================================================
    async def list_direct_outsource_sendable(
        self,
        *,
        process_ids: list[int],
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[tuple[TPartBatch, TPart]]:
        """直接发送外协候选（无需审批工序的两种来源状态合并）。行=批次（2026-07-29 批次化）。

        谓词（2026-07-29 批次化；2026-09-16 PR-3 派生 step.process_id）：
        - deleted_at IS NULL
        - t_process_chain_step.process_id IN process_ids（由 batch.current_process_step_id 派生）
        - TPartBatch.status = PENDING（起始外协，OFFICE）
        - OR (TPartBatch.status = IN_PROCESS + TPartBatch.location = PRODUCTION_SHELF)（中间外协）

        C2 货架前置**只**保留在 send_to_outsource 服务层校验（中间外协路径）。
        """
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            # 2026-09-16 PR-3：next_process_id 列已删，改为派生 step.process_id。
            _chain_step_process_subq().in_(process_ids),
            or_(
                TPartBatch.status == "PENDING",
                and_(
                    TPartBatch.status == "IN_PROCESS",
                    TPartBatch.location == "PRODUCTION_SHELF",
                ),
            ),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"%{kw}%")
                )
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [(b, p) for p, b in result.unique().all()]

    async def count_direct_outsource_sendable(
        self,
        *,
        process_ids: list[int],
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        """直接发送候选总数（按批次计；与 list_direct_outsource_sendable 同谓词）。"""
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            # 2026-09-16 PR-3：next_process_id 列已删，改为派生 step.process_id。
            _chain_step_process_subq().in_(process_ids),
            or_(
                TPartBatch.status == "PENDING",
                and_(
                    TPartBatch.status == "IN_PROCESS",
                    TPartBatch.location == "PRODUCTION_SHELF",
                ),
            ),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"{kw}%")
                )
        stmt = stmt.with_only_columns(func.count(TPartBatch.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_approved_outsource_sendable(
        self,
        *,
        part_ids: list[int],
        process_ids: list[int],
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[tuple[TPartBatch, TPart]]:
        """审批后外协可发送候选（需审批工序 + 有 APPROVED 报价 + 起始 / 中间外协来源）。行=批次。

        谓词（2026-07-29 批次化；2026-09-16 PR-3 派生 step.process_id）：
        - deleted_at IS NULL
        - TPart.id IN part_ids（service 预筛：至少有 1 条 APPROVED 报价的 part_id）
        - t_process_chain_step.process_id IN process_ids（由 batch.current_process_step_id 派生；service 预筛：这些工序需要审批）
        - TPartBatch.status = PENDING（起始外协审批）
        - OR (TPartBatch.status = IN_PROCESS + TPartBatch.location = PRODUCTION_SHELF)（中间外协审批）
        """
        if not part_ids or not process_ids:
            return []
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPart.id.in_(part_ids),
            # 2026-09-16 PR-3：next_process_id 列已删，改为派生 step.process_id。
            _chain_step_process_subq().in_(process_ids),
            or_(
                TPartBatch.status == "PENDING",
                and_(
                    TPartBatch.status == "IN_PROCESS",
                    TPartBatch.location == "PRODUCTION_SHELF",
                ),
            ),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"{kw}%")
                )
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [(b, p) for p, b in result.unique().all()]

    async def count_approved_outsource_sendable(
        self,
        *,
        part_ids: list[int],
        process_ids: list[int],
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        """审批后候选总数（按批次计；与 list_approved_outsource_sendable 同谓词）。"""
        if not part_ids or not process_ids:
            return 0
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPart.id.in_(part_ids),
            # 2026-09-16 PR-3：next_process_id 列已删，改为派生 step.process_id。
            _chain_step_process_subq().in_(process_ids),
            or_(
                TPartBatch.status == "PENDING",
                and_(
                    TPartBatch.status == "IN_PROCESS",
                    TPartBatch.location == "PRODUCTION_SHELF",
                ),
            ),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"{kw}%")
                )
        stmt = stmt.with_only_columns(func.count(TPartBatch.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())


    # 2026-09-16 删除 `list_outsource_receivable`（t_part 瘦身，Rust 迁移 027）：
    # 其 WHERE 依赖已删的 t_part.location 列（status='OUTSOURCE' +
    # location='OUTSOURCE_COMPANY'），且全仓已无调用方（v1 外协接收端点
    # dormant），随删列一并移除，不再保留炸弹。

    # ============================================================
    # 新建报价 picker 默认筛选（PR-H 2026-07-28）
    # ============================================================
    async def list_quotable_for_outsource_quote(
        self,
        *,
        keyword: str | None = None,
        shelf_ids_in: list[int],
        limit: int = 500,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[tuple[TPartBatch, TPart]]:
        """新建外协报价对话框零件 picker 默认数据源。行=批次（2026-07-29 批次化）。

        谓词：
          - TPartBatch.status='IN_PROCESS' AND TPartBatch.location='PRODUCTION_SHELF'
          - AND TPartBatch.current_holder_id IN shelf_ids_in（已在外协工序货架上）
          - 可选 keyword 模糊（drawing_no / name / serial_no）

        排序：TPartBatch.created_at DESC, TPartBatch.id DESC（最新在工先）。
        `shelf_ids_in` 由 service 从 shelf_process.list_shelf_ids_with_process_category
        传入；本方法不交叉工序类别判定，避免重复 join。

        返回 (TPartBatch, TPart) 元组列表，service 层用批次字段拼 PartListItem。
        """
        if not shelf_ids_in:
            return []
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPartBatch.status == "IN_PROCESS",
            TPartBatch.location == "PRODUCTION_SHELF",
            TPartBatch.current_holder_id.in_(shelf_ids_in),
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        if keyword:
            kw = keyword.strip()
            if kw:
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                    | TPart.serial_no.ilike(f"{kw}%")
                )
        stmt = stmt.order_by(
            TPartBatch.created_at.desc(),
            TPartBatch.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [(b, p) for p, b in result.unique().all()]


def _and_chained(*clauses):
    """简易 AND 链组合（与 SQLAlchemy 的 and_ 等价但避免 import 冲突）。"""
    from sqlalchemy import and_
    return and_(*clauses)