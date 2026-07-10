from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TWorker


class WorkerRepository:
    """t_worker 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, worker: TWorker) -> TWorker:
        self.session.add(worker)
        await self.session.flush()
        return worker

    # ===== 单条 =====
    async def get_by_id(
        self, worker_id: int, *, include_deleted: bool = False
    ) -> TWorker | None:
        result = await self.session.get(TWorker, worker_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_badge_code(
        self, badge_code: str, *, include_deleted: bool = False
    ) -> TWorker | None:
        stmt = select(TWorker).where(TWorker.badge_code == badge_code)
        if not include_deleted:
            stmt = stmt.where(TWorker.deleted_at.is_(None))
        stmt = stmt.order_by(TWorker.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_card_no(
        self, id_card_no: str, *, include_deleted: bool = False
    ) -> TWorker | None:
        """按身份证号定位工人（id_card_no 通过联合唯一索引保证全局唯一）。"""
        stmt = select(TWorker).where(TWorker.id_card_no == id_card_no)
        if not include_deleted:
            stmt = stmt.where(TWorker.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_ids(
        self, worker_ids: list[int], *, include_deleted: bool = False
    ) -> list[TWorker]:
        if not worker_ids:
            return []
        stmt = select(TWorker).where(TWorker.id.in_(worker_ids))
        if not include_deleted:
            stmt = stmt.where(TWorker.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表 =====
    async def list_with_filters(
        self,
        *,
        name_like: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TWorker]:
        stmt = select(TWorker)
        if not include_deleted:
            stmt = stmt.where(TWorker.deleted_at.is_(None))
        if name_like:
            stmt = stmt.where(TWorker.name.ilike(f"%{name_like}%"))
        if is_active is not None:
            stmt = stmt.where(TWorker.is_active.is_(is_active))
        stmt = stmt.order_by(TWorker.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        name_like: str | None = None,
        is_active: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TWorker)
        if not include_deleted:
            stmt = stmt.where(TWorker.deleted_at.is_(None))
        if name_like:
            stmt = stmt.where(TWorker.name.ilike(f"%{name_like}%"))
        if is_active is not None:
            stmt = stmt.where(TWorker.is_active.is_(is_active))
        stmt = stmt.with_only_columns(func.count(TWorker.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 软删 =====
    async def update(self, worker: TWorker) -> TWorker:
        await self.session.flush()
        return worker

    async def soft_delete(self, worker: TWorker) -> TWorker:
        worker.deleted_at = now_naive()
        worker.is_active = False
        await self.session.flush()
        return worker