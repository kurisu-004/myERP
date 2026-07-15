"""外协公司 ↔ 工序 多对多映射 (OutsourceCompanyProcess) 数据访问。

镜像 WorkTypeProcessRepository：list_by_company / list_by_process / 整体替换用的
delete_by_company / 软单行 + 反向查询（list_companys_by_process，给发送外协时
按工序过滤候选公司用）。
"""
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TOutsourceCompanyProcess


class OutsourceCompanyProcessRepository:
    """t_outsource_company_process 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(
        self, row: TOutsourceCompanyProcess,
    ) -> TOutsourceCompanyProcess:
        self.session.add(row)
        await self.session.flush()
        return row

    # ===== 列表 =====
    async def list_by_outsource_company(
        self,
        company_id: int,
        *,
        include_deleted: bool = False,
    ) -> list[TOutsourceCompanyProcess]:
        stmt = select(TOutsourceCompanyProcess).where(
            TOutsourceCompanyProcess.outsource_company_id == company_id,
        )
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompanyProcess.deleted_at.is_(None))
        stmt = stmt.order_by(
            TOutsourceCompanyProcess.sort_order.asc(),
            TOutsourceCompanyProcess.id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_process_ids_by_outsource_company(
        self,
        company_id: int,
        *,
        include_deleted: bool = False,
    ) -> list[int]:
        """发送外协时校验：取某公司映射的 process_id 集合（去重）。"""
        stmt = select(TOutsourceCompanyProcess.process_id).where(
            TOutsourceCompanyProcess.outsource_company_id == company_id,
        )
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompanyProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return [int(pid) for pid in result.scalars().all()]

    async def list_companys_by_process(
        self,
        process_id: int,
        *,
        include_deleted: bool = False,
    ) -> list[TOutsourceCompanyProcess]:
        """发送外协对话框：按工序反查能做此工序的所有公司（含软删过滤）。
        返回的 junction 行可在 service 层 join 出 outsource company 行。"""
        stmt = select(TOutsourceCompanyProcess).where(
            TOutsourceCompanyProcess.process_id == process_id,
        )
        if not include_deleted:
            stmt = stmt.where(TOutsourceCompanyProcess.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 集合替换 =====
    async def delete_by_outsource_company(self, company_id: int) -> None:
        """把某公司的全部映射置为软删。

        改为 ORM 循环：service 层先 `row.updated_by = self._user_id`
        再调用本方法，让 audit 字段与 deleted_at 同步写入。数据量小
        （单公司映射行数通常 < 20），事务原子性仍由 `session.flush()`
        在外层保证。
        """
        rows = await self.list_by_outsource_company(
            company_id, include_deleted=False,
        )
        for row in rows:
            row.deleted_at = now_naive()
            await self.session.flush()

    async def hard_delete_by_outsource_company(self, company_id: int) -> None:
        """物理删除（仅 migration 清理用，service 不调用）。"""
        stmt = sa_delete(TOutsourceCompanyProcess).where(
            TOutsourceCompanyProcess.outsource_company_id == company_id,
        )
        await self.session.execute(stmt)
        await self.session.flush()

    # ===== 更新 / 软删 =====
    async def update(
        self, row: TOutsourceCompanyProcess,
    ) -> TOutsourceCompanyProcess:
        await self.session.flush()
        return row

    async def soft_delete(
        self, row: TOutsourceCompanyProcess,
    ) -> TOutsourceCompanyProcess:
        row.deleted_at = now_naive()
        await self.session.flush()
        return row