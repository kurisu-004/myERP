from typing import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TPart
from model.enums import PartSortKey, PartStatus, SortDir


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
        # 2026-09-24 PR-3 review 第 1 轮修复：删除 `has_outsource_history` 参
        # （其 WHERE 依赖已删的 TPartEvent / PartEventType；v1 外协历史端点
        # 已 dormant，业务由 backend-rust v2 承接）。
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
        # 2026-09-24 PR-3 review 第 1 轮修复：删除 `has_outsource_history` 参
        # （其 WHERE 依赖已删的 TPartEvent / PartEventType；v1 外协历史端点
        # 已 dormant，业务由 backend-rust v2 承接）。
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

    # 2026-09-24 PR-3 review 第 1 轮修复：删除 `find_delivered_older_than`
    # （dormant helper，依赖已删的 TPartEvent / PartEventType；v1 auto_complete
    # 服务已下线，业务由 backend-rust v2 task/auto_complete.rs 接管）。

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
        # 2026-09-24 PR-3 review 第 1 轮修复：删除 `has_outsource_history` 参
        # （其 WHERE 依赖已删的 TPartEvent / PartEventType）。
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
        # 2026-09-24 PR-3 review 第 1 轮修复：删除 `has_outsource_history` 过滤分支
        # （WHERE 依赖已删的 TPartEvent / PartEventType；v1 外协历史端点已 dormant）。
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

    # 2026-09-24 PR-3 review 第 1 轮修复：删除全部 dormant 外协 helper 方法
    # （`_build_outsource_sendable_stmt` / `list_outsource_sendable` /
    #  `count_outsource_sendable` / `list_direct_outsource_candidates` /
    #  `count_direct_outsource_candidates` / `list_direct_outsource_sendable` /
    #  `count_direct_outsource_sendable` / `list_approved_outsource_sendable` /
    #  `count_approved_outsource_sendable` / `list_quotable_for_outsource_quote`）
    # —— 这些方法依赖已删的 `TProcessChainStep` ORM（2026-09-24 PR-3 dormant
    # 下线）+ `_chain_step_process_subq()`；v1 外协业务路由已下线，业务由
    # backend-rust v2 承接，全仓无调用方，留着只会有 latent NameError。

    # 2026-09-16 删除 `list_outsource_receivable`（t_part 瘦身，Rust 迁移 027）：
    # 其 WHERE 依赖已删的 t_part.location 列（status='OUTSOURCE' +
    # location='OUTSOURCE_COMPANY'），且全仓已无调用方（v1 外协接收端点
    # dormant），随删列一并移除，不再保留炸弹。
