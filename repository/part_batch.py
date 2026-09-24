"""零件数量批次仓储（2026-07-29 批次化新增）。

查询约定：
- 默认过滤 `deleted_at IS NULL`；批次永不物理删除（取消走 CANCELLED 终态）。
- 列表方法多返回 `(TPartBatch, TPart)` 二元组：批次是数量/状态载体，
  工单提供展示字段（图号 / 名称 / 加急 / 交期），一次 JOIN 避免 N+1。
- 拆分并发：`get_for_update` 锁源批次行 → 同事单的拆分串行化，
  `next_batch_no` 在锁内取 MAX+1，保证 (part_id, batch_no) 唯一。
"""
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart, TPartBatch
from model.enums import PartStatus


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

    async def sum_delivered_by_part_ids(
        self, part_ids: list[int]
    ) -> dict[int, int]:
        """批量返回每个工单的已送数量：未软删批次中 status ∈ (DELIVERED, COMPLETED) 的 quantity 之和。"""
        if not part_ids:
            return {}
        stmt = (
            select(TPartBatch.part_id, func.sum(TPartBatch.quantity))
            .where(
                TPartBatch.part_id.in_(part_ids),
                TPartBatch.status.in_(
                    [PartStatus.DELIVERED.value, PartStatus.COMPLETED.value]
                ),
                TPartBatch.deleted_at.is_(None),
            )
            .group_by(TPartBatch.part_id)
        )
        result = await self.session.execute(stmt)
        return {int(pid): int(total) for pid, total in result.all()}

    async def next_batch_no(self, part_id: int) -> int:
        """下一个批次序号（MAX+1）。须在拆分锁内调用；取消的批次不占号不复用。"""
        stmt = select(
            func.coalesce(func.max(TPartBatch.batch_no), 0) + 1
        ).where(TPartBatch.part_id == part_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # 2026-09-24 PR-3 review 第 1 轮修复：删除 `list_for_work_type` /
    # `list_for_work_type_all_shelves`（dormant v1 扫码台查询，依赖已删的
    #  `_chain_step_process_subq()` + `TProcessChainStep` ORM；v1 扫码端点已
    # dormant，业务由 backend-rust v2 承接，全仓无调用方）。

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

    async def count_held_by_worker(self, *, worker_id: int) -> int:
        """2026-08-05：工种可领取上限校验用 — 工人当前持有的活跃批次数。

        条件同 `list_held_by_worker`（status=IN_PROCESS + location=WORKER +
        holder=worker_id + 未删）。worker_id 缺失返回 0。
        """
        if not worker_id:
            return 0
        stmt = (
            select(func.count(TPartBatch.id))
            .where(
                TPartBatch.status == "IN_PROCESS",
                TPartBatch.location == "WORKER",
                TPartBatch.current_holder_id == worker_id,
                TPartBatch.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_batches_with_part(
        self,
        *,
        statuses: list[str] | None = None,
        customer_ids_in: list[int] | None = None,
        keyword: str | None = None,
        serial_no: str | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
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
                or_(
                    TPart.drawing_no.ilike(like),
                    TPart.name.ilike(like),
                    TPart.serial_no.ilike(like),
                )
            )
        if serial_no:
            stmt = stmt.where(TPart.serial_no.ilike(f"%{serial_no.strip()}%"))
        if planned_delivery_date_from is not None:
            stmt = stmt.where(TPart.planned_delivery_date >= planned_delivery_date_from)
        if planned_delivery_date_to is not None:
            stmt = stmt.where(TPart.planned_delivery_date <= planned_delivery_date_to)
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
        serial_no: str | None = None,
        planned_delivery_date_from: date | None = None,
        planned_delivery_date_to: date | None = None,
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
                or_(
                    TPart.drawing_no.ilike(like),
                    TPart.name.ilike(like),
                    TPart.serial_no.ilike(like),
                )
            )
        if serial_no:
            stmt = stmt.where(TPart.serial_no.ilike(f"%{serial_no.strip()}%"))
        if planned_delivery_date_from is not None:
            stmt = stmt.where(TPart.planned_delivery_date >= planned_delivery_date_from)
        if planned_delivery_date_to is not None:
            stmt = stmt.where(TPart.planned_delivery_date <= planned_delivery_date_to)
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

    # 2026-09-24 PR-3 review 第 1 轮修复：删除 `find_delivered_older_than`
    # （dormant helper，依赖已删的 TPartEvent / PartEventType；v1 auto_complete
    # 服务已下线，业务由 backend-rust v2 task/auto_complete.rs 接管）。
