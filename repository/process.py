"""工序 (Process) 数据访问。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TProcess


class ProcessRepository:
    """t_process 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, process: TProcess) -> TProcess:
        self.session.add(process)
        await self.session.flush()
        return process

    # ===== 单条 =====
    async def get_by_id(
        self, process_id: int, *, include_deleted: bool = False
    ) -> TProcess | None:
        result = await self.session.get(TProcess, process_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_code(
        self, code: str, *, include_deleted: bool = False
    ) -> TProcess | None:
        stmt = select(TProcess).where(TProcess.code == code)
        if not include_deleted:
            stmt = stmt.where(TProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_ids(
        self, process_ids: list[int], *, include_deleted: bool = False
    ) -> list[TProcess]:
        if not process_ids:
            return []
        stmt = select(TProcess).where(TProcess.id.in_(process_ids))
        if not include_deleted:
            stmt = stmt.where(TProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 列表 =====
    async def list_with_filters(
        self,
        *,
        code_like: str | None = None,
        category: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TProcess]:
        stmt = select(TProcess)
        if not include_deleted:
            stmt = stmt.where(TProcess.deleted_at.is_(None))
        if code_like:
            stmt = stmt.where(TProcess.code.ilike(f"%{code_like}%"))
        if category:
            stmt = stmt.where(TProcess.category == category)
        stmt = stmt.order_by(TProcess.sort_order.asc(), TProcess.id.asc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        code_like: str | None = None,
        category: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TProcess)
        if not include_deleted:
            stmt = stmt.where(TProcess.deleted_at.is_(None))
        if code_like:
            stmt = stmt.where(TProcess.code.ilike(f"%{code_like}%"))
        if category:
            stmt = stmt.where(TProcess.category == category)
        stmt = stmt.with_only_columns(func.count(TProcess.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 软删 =====
    async def update(self, process: TProcess) -> TProcess:
        await self.session.flush()
        return process

    async def soft_delete(self, process: TProcess) -> TProcess:
        process.deleted_at = now_naive()
        await self.session.flush()
        return process