"""货架 ↔ 工序 映射 (ShelfProcess) 数据访问。

热点路径：
- `list_process_ids_by_shelf` — RETURN 扫码台过滤要用的纯 ID 集
- `set_for_shelf` — Manager 维护映射的「整体替换」语义
"""
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TShelfProcess


class ShelfProcessRepository:
    """t_shelf_process 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, row: TShelfProcess) -> TShelfProcess:
        self.session.add(row)
        await self.session.flush()
        return row

    # ===== 单条 =====
    async def get(
        self, shelf_id: int, process_id: int, *, include_deleted: bool = False
    ) -> TShelfProcess | None:
        stmt = select(TShelfProcess).where(
            TShelfProcess.shelf_id == shelf_id,
            TShelfProcess.process_id == process_id,
        )
        if not include_deleted:
            stmt = stmt.where(TShelfProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表 =====
    async def list_by_shelf(
        self, shelf_id: int, *, include_deleted: bool = False
    ) -> list[TShelfProcess]:
        stmt = select(TShelfProcess).where(
            TShelfProcess.shelf_id == shelf_id,
        )
        if not include_deleted:
            stmt = stmt.where(TShelfProcess.deleted_at.is_(None))
        stmt = stmt.order_by(
            TShelfProcess.sort_order.asc(),
            TShelfProcess.id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_process(
        self, process_id: int, *, include_deleted: bool = False
    ) -> list[TShelfProcess]:
        stmt = select(TShelfProcess).where(
            TShelfProcess.process_id == process_id,
        )
        if not include_deleted:
            stmt = stmt.where(TShelfProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_process_ids_by_shelf(
        self, shelf_id: int, *, include_deleted: bool = False
    ) -> list[int]:
        """RETURN 扫码台过滤用：取某货架映射的 process_id 集合（去重）。"""
        stmt = select(TShelfProcess.process_id).where(
            TShelfProcess.shelf_id == shelf_id,
        )
        if not include_deleted:
            stmt = stmt.where(TShelfProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return [int(pid) for pid in result.scalars().all()]

    async def list_shelf_ids_with_process_category(
        self, category: str, *, include_deleted: bool = False,
    ) -> list[int]:
        """反向按工序类别查架：返回所有「绑定了 category 类别工序」active 货架 id 集合（去重）。

        用于：
          - 发外协：源货架必须在 OUTSOURCE 集合中
          - 新建报价 picker 默认筛选：候选零件 current_holder_id 必须在 OUTSOURCE 集合中

        2026-07-28 PR-H 重构"外协统一走外协工序货架"时新增。
        """
        from model import TProcess
        from sqlalchemy import distinct

        stmt = (
            select(distinct(TShelfProcess.shelf_id))
            .join(TProcess, TProcess.id == TShelfProcess.process_id)
            .where(TProcess.category == category)
            .where(TProcess.deleted_at.is_(None))
        )
        if not include_deleted:
            stmt = stmt.where(TShelfProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return [int(sid) for sid in result.scalars().all()]

    async def list_all_mappings(self) -> dict[int, list[int]]:
        """批量取所有 active 映射，返回 `{shelf_id: [process_id, ...]}`。

        按 `shelf_id ASC, sort_order ASC` 排序——前端
        `useShelfProcessFilter` 用此方法做一次性反向索引，避免 N+1。
        """
        stmt = (
            select(
                TShelfProcess.shelf_id,
                TShelfProcess.process_id,
            )
            .where(TShelfProcess.deleted_at.is_(None))
            .order_by(
                TShelfProcess.shelf_id.asc(),
                TShelfProcess.sort_order.asc(),
            )
        )
        out: dict[int, list[int]] = {}
        for sid, pid in (await self.session.execute(stmt)).all():
            out.setdefault(int(sid), []).append(int(pid))
        return out

    async def list_mapped_process_codes_by_shelf_ids(
        self, shelf_ids: list[int]
    ) -> dict[int, list[str]]:
        """批量取每架的 mapped process code 列表（按 sort_order 排）。

        Returns: `{shelf_id: [process_code, ...]}`；传入 id 不在结果中时为空 list。
        共享 HMI picker 用：service 一次性拿所有候选架的工序 code 列表，
        避免 N+1。
        """
        out: dict[int, list[str]] = {sid: [] for sid in shelf_ids}
        if not shelf_ids:
            return out
        from model import TProcess

        stmt = (
            select(TShelfProcess.shelf_id, TProcess.code)
            .join(TProcess, TProcess.id == TShelfProcess.process_id)
            .where(
                TShelfProcess.shelf_id.in_(shelf_ids),
                TShelfProcess.deleted_at.is_(None),
            )
            .order_by(
                TShelfProcess.shelf_id.asc(),
                TShelfProcess.sort_order.asc(),
            )
        )
        result = await self.session.execute(stmt)
        for shelf_id, code in result.all():
            out[int(shelf_id)].append(code)
        return out

    # ===== 集合替换（Manager 维护映射用）=====
    async def delete_by_shelf(self, shelf_id: int) -> None:
        """把某货架的全部映射置为软删。

        改为 ORM 循环：service 层先 `row.updated_by = self._user_id`
        再调用本方法，让 audit 字段与 deleted_at 同步写入。数据量小
        （单货架映射行数通常 < 20），事务原子性仍由 `session.flush()`
        在外层保证。
        """
        rows = await self.list_by_shelf(
            shelf_id, include_deleted=False,
        )
        for row in rows:
            row.deleted_at = now_naive()
            await self.session.flush()

    async def hard_delete_by_shelf(self, shelf_id: int) -> None:
        """物理删除（仅 migration 清理用，service 不调用）。"""
        stmt = sa_delete(TShelfProcess).where(
            TShelfProcess.shelf_id == shelf_id,
        )
        await self.session.execute(stmt)
        await self.session.flush()

    # ===== 更新 / 软删 =====
    async def update(self, row: TShelfProcess) -> TShelfProcess:
        await self.session.flush()
        return row

    async def soft_delete(self, row: TShelfProcess) -> TShelfProcess:
        row.deleted_at = now_naive()
        await self.session.flush()
        return row
