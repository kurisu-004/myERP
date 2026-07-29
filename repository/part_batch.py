"""零件数量批次仓储（2026-07-29 批次化新增）。

查询约定：
- 默认过滤 `deleted_at IS NULL`；批次永不物理删除（取消走 CANCELLED 终态）。
- 列表方法多返回 `(TPartBatch, TPart)` 二元组：批次是数量/状态载体，
  工单提供展示字段（图号 / 名称 / 加急 / 交期），一次 JOIN 避免 N+1。
- 拆分并发：`get_for_update` 锁源批次行 → 同事单的拆分串行化，
  `next_batch_no` 在锁内取 MAX+1，保证 (part_id, batch_no) 唯一。
"""
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart, TPartBatch, TPartEvent
from model.enums import PartEventType, PartStatus


class PartBatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, batch: TPartBatch) -> TPartBatch:
        self.session.add(batch)
        await self.session.flush()
        return batch

    async def update(self, batch: TPartBatch) -> TPartBatch:
        await self.session.flush()
        return batch

    async def soft_delete(self, batch: TPartBatch) -> None:
        from core.time import now_naive
        batch.deleted_at = now_naive()
        await self.session.flush()

    # ===== 单条查询 =====
    async def get_by_id(
        self, batch_id: int, *, include_deleted: bool = False
    ) -> TPartBatch | None:
        batch = await self.session.get(TPartBatch, batch_id)
        if batch is None:
            return None
        if not include_deleted and batch.deleted_at is not None:
            return None
        return batch

    async def get_for_update(self, batch_id: int) -> TPartBatch | None:
        """拆分 / 部分流转前锁源批次行（SELECT ... FOR UPDATE）。"""
        stmt = (
            select(TPartBatch)
            .where(TPartBatch.id == batch_id, TPartBatch.deleted_at.is_(None))
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 工单内批次 =====
    async def list_by_part(
        self, part_id: int, *, include_deleted: bool = False
    ) -> list[TPartBatch]:
        """工单的全部批次，按 batch_no 升序（批次树 / 详情页监控用）。"""
        stmt = select(TPartBatch).where(TPartBatch.part_id == part_id)
        if not include_deleted:
            stmt = stmt.where(TPartBatch.deleted_at.is_(None))
        stmt = stmt.order_by(TPartBatch.batch_no.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_by_part_ids(
        self, part_ids: list[int]
    ) -> list[TPartBatch]:
        """多个工单的未软删批次（rollup / by-serial 批次摘要批查，防 N+1）。"""
        if not part_ids:
            return []
        stmt = (
            select(TPartBatch)
            .where(
                TPartBatch.part_id.in_(part_ids),
                TPartBatch.deleted_at.is_(None),
            )
            .order_by(TPartBatch.part_id.asc(), TPartBatch.batch_no.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def next_batch_no(self, part_id: int) -> int:
        """下一个批次序号（MAX+1）。须在拆分锁内调用；取消的批次不占号不复用。"""
        stmt = select(
            func.coalesce(func.max(TPartBatch.batch_no), 0) + 1
        ).where(TPartBatch.part_id == part_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 扫码台 / 列表（批次级，JOIN 工单拿展示字段）=====
    async def list_for_work_type(
        self,
        *,
        shelf_id: int,
        mapped_process_ids: list[int],
    ) -> list[tuple[TPartBatch, TPart]]:
        """PICK_UP：某生产货架上、某工种可领的批次（含工单）。

        过滤同 PartRepository.list_for_work_type，只是主体换成批次。
        """
        if not mapped_process_ids:
            return []
        stmt = (
            select(TPartBatch, TPart)
            .join(TPart, TPart.id == TPartBatch.part_id)
            .where(
                TPartBatch.status == "IN_PROCESS",
                TPartBatch.location == "PRODUCTION_SHELF",
                TPartBatch.current_holder_id == shelf_id,
                or_(
                    TPartBatch.next_process_id.is_(None),
                    TPartBatch.next_process_id.in_(mapped_process_ids),
                ),
                TPartBatch.deleted_at.is_(None),
                TPart.deleted_at.is_(None),
            )
            .order_by(
                TPart.is_urgent.desc(),
                TPart.planned_delivery_date.asc(),
                TPartBatch.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def list_for_work_type_all_shelves(
        self,
        *,
        shelf_ids: list[int],
        mapped_process_ids: list[int],
    ) -> list[tuple[TPartBatch, TPart]]:
        """跨货架可领批次（扫码台默认视图）。"""
        if not mapped_process_ids or not shelf_ids:
            return []
        stmt = (
            select(TPartBatch, TPart)
            .join(TPart, TPart.id == TPartBatch.part_id)
            .where(
                TPartBatch.status == "IN_PROCESS",
                TPartBatch.location == "PRODUCTION_SHELF",
                TPartBatch.current_holder_id.in_(shelf_ids),
                or_(
                    TPartBatch.next_process_id.is_(None),
                    TPartBatch.next_process_id.in_(mapped_process_ids),
                ),
                TPartBatch.deleted_at.is_(None),
                TPart.deleted_at.is_(None),
            )
            .order_by(
                TPart.is_urgent.desc(),
                TPart.planned_delivery_date.asc(),
                TPartBatch.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def list_held_by_worker(
        self, *, worker_id: int
    ) -> list[tuple[TPartBatch, TPart]]:
        """RETURN / INSPECT：当前由某工人持有的批次（含工单）。"""
        if not worker_id:
            return []
        stmt = (
            select(TPartBatch, TPart)
            .join(TPart, TPart.id == TPartBatch.part_id)
            .where(
                TPartBatch.status == "IN_PROCESS",
                TPartBatch.location == "WORKER",
                TPartBatch.current_holder_id == worker_id,
                TPartBatch.deleted_at.is_(None),
                TPart.deleted_at.is_(None),
            )
            .order_by(
                TPart.is_urgent.desc(),
                TPart.planned_delivery_date.asc(),
                TPartBatch.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def list_batches_with_part(
        self,
        *,
        statuses: list[str] | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[tuple[TPartBatch, TPart]]:
        """批次级通用列表（品检待办 / 送货单候选共用）。

        过滤：批次 status 集合 + 工单客户集合 + 工单图号/名称模糊。
        排序：加急优先 → 临期优先 → 稳定 id。
        """
        stmt = (
            select(TPartBatch, TPart)
            .join(TPart, TPart.id == TPartBatch.part_id)
            .where(TPartBatch.deleted_at.is_(None), TPart.deleted_at.is_(None))
        )
        if statuses:
            stmt = stmt.where(TPartBatch.status.in_(statuses))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                or_(TPart.drawing_no.ilike(like), TPart.name.ilike(like))
            )
        stmt = stmt.order_by(
            TPart.is_urgent.desc(),
            TPart.planned_delivery_date.asc(),
            TPartBatch.id.asc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def count_batches_with_part(
        self,
        *,
        statuses: list[str] | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
    ) -> int:
        stmt = (
            select(func.count(TPartBatch.id))
            .join(TPart, TPart.id == TPartBatch.part_id)
            .where(TPartBatch.deleted_at.is_(None), TPart.deleted_at.is_(None))
        )
        if statuses:
            stmt = stmt.where(TPartBatch.status.in_(statuses))
        if customer_ids_in:
            stmt = stmt.where(TPart.customer_id.in_(customer_ids_in))
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                or_(TPart.drawing_no.ilike(like), TPart.name.ilike(like))
            )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 送货单 =====
    async def list_by_delivery_note(
        self, note_id: int
    ) -> list[tuple[TPartBatch, TPart]]:
        """送货单行项目（批次级）。"""
        stmt = (
            select(TPartBatch, TPart)
            .join(TPart, TPart.id == TPartBatch.part_id)
            .where(
                TPartBatch.delivery_note_id == note_id,
                TPartBatch.deleted_at.is_(None),
                TPart.deleted_at.is_(None),
            )
            .order_by(TPartBatch.id.asc())
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    # ===== auto_complete（DELIVERED 批次超阈值 → COMPLETED）=====
    async def find_delivered_older_than(
        self, threshold: datetime
    ) -> list[TPartBatch]:
        """DELIVERED 且最近一次送货事件早于 threshold、之后未返修的批次。

        镜像 PartRepository.find_delivered_older_than 的逻辑，事件按 batch_id
        匹配（历史事件已回填根批次 id）。
        """
        latest_delivered = (
            select(func.max(TPartEvent.created_at))
            .where(
                TPartEvent.batch_id == TPartBatch.id,
                TPartEvent.event_type == PartEventType.STATUS_CHANGED.value,
                TPartEvent.from_status == PartStatus.READY_TO_SHIP.value,
                TPartEvent.to_status == PartStatus.DELIVERED.value,
            )
            .correlate(TPartBatch)
            .scalar_subquery()
        )
        repair_after = (
            select(TPartEvent.id)
            .where(
                TPartEvent.batch_id == TPartBatch.id,
                TPartEvent.event_type == PartEventType.REPAIR_STARTED.value,
                TPartEvent.created_at > latest_delivered,
            )
            .correlate(TPartBatch)
            .exists()
        )
        stmt = select(TPartBatch).where(
            TPartBatch.status == PartStatus.DELIVERED.value,
            TPartBatch.deleted_at.is_(None),
            latest_delivered.is_not(None),
            latest_delivered <= threshold,
            ~repair_after,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
