from datetime import datetime

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TPart, TPartBatch, TPartEvent
from model.enums import PartEventType, PartLocation, PartSortKey, PartStatus, SortDir


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
        next_process_ids: list[int] | None = None,  # 2026-08-01：下一道工序多选
        locations: list[PartLocation] | None = None,  # 2026-08-01：物理位置多选
        sort_by: PartSortKey = PartSortKey.PLANNED_DELIVERY_DATE,
        sort_dir: SortDir = SortDir.ASC,
        include_deleted: bool = False,
        assembly_id_is_null: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TPart]:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            statuses=statuses,
            is_urgent=is_urgent,
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
            next_process_ids=next_process_ids,
            locations=locations,
            include_deleted=include_deleted,
            assembly_id_is_null=assembly_id_is_null,
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
        next_process_ids: list[int] | None = None,  # 2026-08-01：下一道工序多选
        locations: list[PartLocation] | None = None,  # 2026-08-01：物理位置多选
        include_deleted: bool = False,
        assembly_id_is_null: bool | None = None,
    ) -> int:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            statuses=statuses,
            is_urgent=is_urgent,
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
            next_process_ids=next_process_ids,
            locations=locations,
            include_deleted=include_deleted,
            assembly_id_is_null=assembly_id_is_null,
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

    # ===== 工人持有件列表（扫码台 RETURN 新流程用，PR-E 2026-07-10）=====
    async def list_held_by_worker(
        self,
        *,
        worker_id: int,
        include_deleted: bool = False,
    ) -> list[TPart]:
        """扫码台 RETURN：列出当前由某工人持有的零件。

        过滤：
        - status = 'IN_PROCESS'（DB 共享状态）
        - location = 'WORKER'（在工人手）
        - current_holder_id = worker_id

        排序：is_urgent DESC, planned_delivery_date ASC, id DESC
        （与 list_for_work_type 一致，加急优先 → 临期优先 → 稳定排序）。

        返回空 list 当 worker_id 为空时（让 service 层短路）。
        """
        if not worker_id:
            return []
        stmt = (
            select(TPart)
            .where(
                TPart.status == "IN_PROCESS",
                TPart.location == "WORKER",
                TPart.current_holder_id == worker_id,
            )
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 工种取件列表（PICK_UP 扫码台热点路径）=====
    async def list_for_work_type(
        self,
        *,
        shelf_id: int,
        mapped_process_ids: list[int],
        include_deleted: bool = False,
    ) -> list[TPart]:
        """扫码台 PICK_UP：列出当前生产货架上、由某工种可领的零件。

        过滤条件：
        - status = 'IN_PROCESS'
        - location = 'PRODUCTION_SHELF'
        - current_holder_id = shelf_id（该零件当前就在指定货架上）
        - next_process_id IS NULL（未指定下一道工序）OR
          next_process_id IN mapped_process_ids（被该工种可领）

        2026-07-21 改：在 SELECT 里附加标量子查询，取该 part 最新一条
        `INSPECTION_FAILED` 事件的 `note`（品检打回备注），作为
        TPart 的 transient 属性 `last_inspection_fail_note` 返回，供
        service 层 `_to_out` 写入 `PartOut.last_inspection_fail_note`。
        transient 属性不会被 SQLAlchemy 视为 dirty。

        排序：is_urgent DESC（加急优先）, planned_delivery_date ASC（临期优先）,
              id DESC（稳定排序）。

        返回空 list 当 mapped_process_ids 为空时（让 service 层短路）。
        """
        if not mapped_process_ids:
            return []
        last_fail_note_subq = (
            select(TPartEvent.note)
            .where(
                TPartEvent.part_id == TPart.id,
                TPartEvent.event_type == PartEventType.INSPECTION_FAILED.value,
            )
            .order_by(TPartEvent.created_at.desc(), TPartEvent.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(TPart, last_fail_note_subq.label("last_inspection_fail_note"))
            .where(
                TPart.status == "IN_PROCESS",
                TPart.location == "PRODUCTION_SHELF",
                TPart.current_holder_id == shelf_id,
                or_(
                    TPart.next_process_id.is_(None),
                    TPart.next_process_id.in_(mapped_process_ids),
                ),
            )
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        )
        result = await self.session.execute(stmt)
        parts: list[TPart] = []
        for row in result.all():
            part = row[0]
            part.last_inspection_fail_note = row[1]  # transient attr
            parts.append(part)
        return parts

    async def list_for_work_type_all_shelves(
        self,
        *,
        mapped_process_ids: list[int],
        shelf_ids: list[int] | None = None,
        include_deleted: bool = False,
    ) -> list[TPart]:
        """共享 HMI PICK_UP 跨架列表：列出 HMI 货架范围内、由某工种可领的零件。

        与 `list_for_work_type` 的差异：
        1. 去掉 `current_holder_id == shelf_id` 单架过滤，改为可选
           `shelf_ids` 多架过滤（None = 全架；空 list = 永远空）；
        2. 前端按 `current_holder_id` 在卡片网格里分组。

        2026-07-21 改：同样附加 last_inspection_fail_note 标量子查询（与
        list_for_work_type 一致）。

        过滤条件：
        - status = 'IN_PROCESS'
        - location = 'PRODUCTION_SHELF'
        - shelf_ids 不为空时：current_holder_id IN shelf_ids
        - next_process_id IS NULL（未指定下一道工序）OR
          next_process_id IN mapped_process_ids（被该工种可领）

        排序：is_urgent DESC（加急优先）, planned_delivery_date ASC（临期优先）,
              id DESC（稳定排序）。

        返回空 list 当 mapped_process_ids 为空，或 shelf_ids 显式传空 list 时
        （让 service 层短路）。
        """
        if not mapped_process_ids:
            return []
        if shelf_ids is not None and not shelf_ids:
            return []
        last_fail_note_subq = (
            select(TPartEvent.note)
            .where(
                TPartEvent.part_id == TPart.id,
                TPartEvent.event_type == PartEventType.INSPECTION_FAILED.value,
            )
            .order_by(TPartEvent.created_at.desc(), TPartEvent.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(TPart, last_fail_note_subq.label("last_inspection_fail_note"))
            .where(
                TPart.status == "IN_PROCESS",
                TPart.location == "PRODUCTION_SHELF",
                or_(
                    TPart.next_process_id.is_(None),
                    TPart.next_process_id.in_(mapped_process_ids),
                ),
            )
        )
        if shelf_ids is not None:
            stmt = stmt.where(TPart.current_holder_id.in_(shelf_ids))
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        )
        result = await self.session.execute(stmt)
        parts: list[TPart] = []
        for row in result.all():
            part = row[0]
            part.last_inspection_fail_note = row[1]
            parts.append(part)
        return parts

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

    # ===== 在架件数批量统计（共享 HMI picker current_load）=====
    async def get_load_map_by_shelf_ids(
        self, shelf_ids: list[int]
    ) -> dict[int, int]:
        """批量取每架的 `current_load`（status=IN_PROCESS + location=PRODUCTION_SHELF 的件数）。

        Returns: `{shelf_id: count}`；传入 id 不在结果中时返回 0。
        service 层给每张卡片填 current_load。
        """
        out: dict[int, int] = {sid: 0 for sid in shelf_ids}
        if not shelf_ids:
            return out
        stmt = (
            select(TPart.current_holder_id, func.count(TPart.id))
            .where(
                TPart.current_holder_id.in_(shelf_ids),
                TPart.status == "IN_PROCESS",
                TPart.location == "PRODUCTION_SHELF",
                TPart.deleted_at.is_(None),
            )
            .group_by(TPart.current_holder_id)
        )
        result = await self.session.execute(stmt)
        for shelf_id, count in result.all():
            out[int(shelf_id)] = int(count)
        return out

    # ===== 内部 =====
    def _build_filter_stmt(
        self,
        *,
        customer_id: int | None,
        customer_ids_in: list[int] | None,
        statuses: list[PartStatus] | None,
        is_urgent: bool | None,
        keyword: str | None,
        order_no: str | None = None,
        serial_no: str | None = None,  # 2026-07-31：序列号独立搜索（ILIKE 包含）
        has_outsource_history: bool | None,
        request_date_from=None,
        request_date_to=None,
        planned_delivery_date_from=None,
        planned_delivery_date_to=None,
        system_delivery_date_from=None,
        system_delivery_date_to=None,
        next_process_ids: list[int] | None = None,  # 2026-08-01：下一道工序多选
        locations: list[PartLocation] | None = None,  # 2026-08-01：物理位置多选
        include_deleted: bool,
        assembly_id_is_null: bool | None = None,
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
        if keyword:
            kw = keyword.strip()
            if kw:
                # drawing_no 走子串包含（ilike '%kw%'）；name 仍按前缀以减小回归面。
                # TODO: 后续把 %/_ 通配符转义（参考 repository/applicant.py:131-133）
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"%{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                )
        # 2026-07-22：订单号独立搜索框（ILIKE 子串包含）。
        if order_no:
            on = order_no.strip()
            if on:
                stmt = stmt.where(TPart.order_no.ilike(f"%{on}%"))
        # 2026-07-31：序列号独立搜索框（ILIKE 子串包含）。
        # 命中子件也算命中（子件 serial_no 形如 {父装配}-{i:02d}）。
        if serial_no:
            sn = serial_no.strip()
            if sn:
                stmt = stmt.where(TPart.serial_no.ilike(f"%{sn}%"))
        # 2026-07-21：PR-F 日期区间筛选（请购日期 / 系统交期）。
        # 仅端点非 None 时加条件；端点为 None 表示半开区间。
        # 系统交期可空（PR-F 字段 NULL=未设置），区间包含 NULL 时也会命中。
        # 用 BETWEEN（含端点）；如只要单向 < 或 >，传 None 即可。
        if request_date_from is not None:
            stmt = stmt.where(TPart.request_date >= request_date_from)
        if request_date_to is not None:
            stmt = stmt.where(TPart.request_date <= request_date_to)
        # 2026-07-22：计划交期区间（planned_delivery_date NOT NULL，无需 NULL 兜底）。
        if planned_delivery_date_from is not None:
            stmt = stmt.where(TPart.planned_delivery_date >= planned_delivery_date_from)
        if planned_delivery_date_to is not None:
            stmt = stmt.where(TPart.planned_delivery_date <= planned_delivery_date_to)
        if system_delivery_date_from is not None:
            stmt = stmt.where(
                or_(
                    TPart.system_delivery_date.is_(None),
                    TPart.system_delivery_date >= system_delivery_date_from,
                )
            )
        if system_delivery_date_to is not None:
            stmt = stmt.where(
                or_(
                    TPart.system_delivery_date.is_(None),
                    TPart.system_delivery_date <= system_delivery_date_to,
                )
            )
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
        # 2026-08-01：下一道工序 / 物理位置多选筛选。
        # 与 `statuses` 语义一致：None / 空列表 = 无筛选；非空列表走 IN 谓词。
        # `next_process_id IS NULL` 的零件会被 SQL `IN` 排除，符合「未指派下一道工序 = 不参与筛选」。
        if next_process_ids:
            stmt = stmt.where(TPart.next_process_id.in_(next_process_ids))
        if locations:
            stmt = stmt.where(
                TPart.location.in_([loc.value for loc in locations])
            )
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
            TPartBatch.next_process_id.in_(process_ids),
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
            TPartBatch.next_process_id.in_(process_ids),
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

        谓词（2026-07-29 批次化；不再要求 c2_shelf_id；起始 / 中间外协都进列表）：
        - deleted_at IS NULL
        - TPartBatch.next_process_id IN process_ids
        - TPartBatch.status = PENDING（起始外协，OFFICE）
        - OR (TPartBatch.status = IN_PROCESS + TPartBatch.location = PRODUCTION_SHELF)（中间外协）

        C2 货架前置**只**保留在 send_to_outsource 服务层校验（中间外协路径）。
        """
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPartBatch.next_process_id.in_(process_ids),
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
            TPartBatch.next_process_id.in_(process_ids),
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

        谓词（2026-07-29 批次化）：
        - deleted_at IS NULL
        - TPart.id IN part_ids（service 预筛：至少有 1 条 APPROVED 报价的 part_id）
        - TPartBatch.next_process_id IN process_ids（service 预筛：这些工序需要审批）
        - TPartBatch.status = PENDING（起始外协审批）
        - OR (TPartBatch.status = IN_PROCESS + TPartBatch.location = PRODUCTION_SHELF)（中间外协审批）
        """
        if not part_ids or not process_ids:
            return []
        stmt = select(TPart, TPartBatch).join(TPartBatch, TPartBatch.part_id == TPart.id).where(
            TPart.id.in_(part_ids),
            TPartBatch.next_process_id.in_(process_ids),
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
            TPartBatch.next_process_id.in_(process_ids),
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


    async def list_outsource_receivable(
        self,
        *,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        is_urgent: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[TPart]:
        """外协接收一览：status='OUTSOURCE' + location='OUTSOURCE_COMPANY' 的零件。

        排序：is_urgent DESC, planned_delivery_date ASC, id DESC
        """
        stmt = select(TPart).where(
            TPart.status == "OUTSOURCE",
            TPart.location == "OUTSOURCE_COMPANY",
        )
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
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
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPart.id.desc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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