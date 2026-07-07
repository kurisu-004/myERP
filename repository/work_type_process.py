"""工种 ↔ 工序 映射 (WorkTypeProcess) 数据访问。

热点路径：
- `list_process_ids_by_work_type` — PICK_UP 扫码台过滤要用的纯 ID 集
- `set_for_work_type` — Manager 维护映射的「整体替换」语义
"""
from datetime import datetime

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TWorkTypeProcess


class WorkTypeProcessRepository:
    """t_work_type_process 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, row: TWorkTypeProcess) -> TWorkTypeProcess:
        self.session.add(row)
        await self.session.flush()
        return row

    # ===== 单条 =====
    async def get(
        self, work_type_id: int, process_id: int, *, include_deleted: bool = False
    ) -> TWorkTypeProcess | None:
        stmt = select(TWorkTypeProcess).where(
            TWorkTypeProcess.work_type_id == work_type_id,
            TWorkTypeProcess.process_id == process_id,
        )
        if not include_deleted:
            stmt = stmt.where(TWorkTypeProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表 =====
    async def list_by_work_type(
        self, work_type_id: int, *, include_deleted: bool = False
    ) -> list[TWorkTypeProcess]:
        stmt = select(TWorkTypeProcess).where(
            TWorkTypeProcess.work_type_id == work_type_id,
        )
        if not include_deleted:
            stmt = stmt.where(TWorkTypeProcess.deleted_at.is_(None))
        stmt = stmt.order_by(
            TWorkTypeProcess.sort_order.asc(),
            TWorkTypeProcess.id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_process(
        self, process_id: int, *, include_deleted: bool = False
    ) -> list[TWorkTypeProcess]:
        stmt = select(TWorkTypeProcess).where(
            TWorkTypeProcess.process_id == process_id,
        )
        if not include_deleted:
            stmt = stmt.where(TWorkTypeProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_process_ids_by_work_type(
        self, work_type_id: int, *, include_deleted: bool = False
    ) -> list[int]:
        """PICK_UP 扫码台过滤用：取某工种映射的 process_id 集合（去重）。"""
        stmt = select(TWorkTypeProcess.process_id).where(
            TWorkTypeProcess.work_type_id == work_type_id,
        )
        if not include_deleted:
            stmt = stmt.where(TWorkTypeProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return [int(pid) for pid in result.scalars().all()]

    # ===== 集合替换（Manager 维护映射用）=====
    async def delete_by_work_type(self, work_type_id: int) -> None:
        """把某工种的全部映射置为软删。

        改为 ORM 循环：service 层先 `row.updated_by = self._user_id`
        再调用本方法，让 audit 字段与 deleted_at 同步写入。数据量小
        （单工种映射行数通常 < 20），事务原子性仍由 `session.flush()`
        在外层保证。
        """
        rows = await self.list_by_work_type(
            work_type_id, include_deleted=False,
        )
        for row in rows:
            row.deleted_at = datetime.utcnow()
            await self.session.flush()

    async def hard_delete_by_work_type(self, work_type_id: int) -> None:
        """物理删除（仅 migration 清理用，service 不调用）。"""
        stmt = sa_delete(TWorkTypeProcess).where(
            TWorkTypeProcess.work_type_id == work_type_id,
        )
        await self.session.execute(stmt)
        await self.session.flush()

    # ===== 更新 / 软删 =====
    async def update(self, row: TWorkTypeProcess) -> TWorkTypeProcess:
        await self.session.flush()
        return row

    async def soft_delete(self, row: TWorkTypeProcess) -> TWorkTypeProcess:
        row.deleted_at = datetime.utcnow()
        await self.session.flush()
        return row