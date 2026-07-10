from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TPart, TPartEvent
from model.enums import PartEventType, PartSortKey, PartStatus, SortDir


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

    # ===== 列表查询（核心：前缀搜索 + 多维过滤 + 排序） =====
    async def list_with_filters(
        self,
        *,
        customer_id: int | None = None,
        customer_ids_in: list[int] | None = None,
        statuses: list[PartStatus] | None = None,
        is_urgent: bool | None = None,
        keyword: str | None = None,
        sort_by: PartSortKey = PartSortKey.PLANNED_DELIVERY_DATE,
        sort_dir: SortDir = SortDir.ASC,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TPart]:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            statuses=statuses,
            is_urgent=is_urgent,
            keyword=keyword,
            include_deleted=include_deleted,
        )
        sort_col = {
            PartSortKey.PLANNED_DELIVERY_DATE: TPart.planned_delivery_date,
            PartSortKey.REQUEST_DATE: TPart.request_date,
            PartSortKey.CREATED_AT: TPart.created_at,
            PartSortKey.SERIAL_NO: TPart.serial_no,
            PartSortKey.DRAWING_NO: TPart.drawing_no,
            PartSortKey.NAME: TPart.name,
        }[sort_by]
        if sort_dir == SortDir.ASC:
            stmt = stmt.order_by(sort_col.asc(), TPart.id.desc())
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
        include_deleted: bool = False,
    ) -> int:
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            customer_ids_in=customer_ids_in,
            statuses=statuses,
            is_urgent=is_urgent,
            keyword=keyword,
            include_deleted=include_deleted,
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

        排序：is_urgent DESC（加急优先）, planned_delivery_date ASC（临期优先）,
              id DESC（稳定排序）。

        返回空 list 当 mapped_process_ids 为空时（让 service 层短路）。
        """
        if not mapped_process_ids:
            return []
        stmt = (
            select(TPart)
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
        return list(result.scalars().all())

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
        stmt = (
            select(TPart)
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
        return list(result.scalars().all())

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
        include_deleted: bool,
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
                stmt = stmt.where(
                    TPart.drawing_no.ilike(f"{kw}%")
                    | TPart.name.ilike(f"{kw}%")
                )
        return stmt