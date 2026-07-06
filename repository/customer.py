from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TCustomer


class CustomerRepository:
    """t_customer 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, customer: TCustomer) -> TCustomer:
        self.session.add(customer)
        await self.session.flush()
        return customer

    async def update(self, customer: TCustomer) -> TCustomer:
        await self.session.flush()
        return customer

    async def soft_delete(self, customer: TCustomer) -> TCustomer:
        from datetime import datetime
        customer.deleted_at = datetime.utcnow()
        await self.session.flush()
        return customer

    # ===== 单条 =====
    async def get_by_id(
        self, customer_id: int, *, include_deleted: bool = False
    ) -> TCustomer | None:
        result = await self.session.get(TCustomer, customer_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    # ===== 批量 =====
    async def list_by_ids(
        self, ids: list[int], *, include_deleted: bool = False
    ) -> list[TCustomer]:
        if not ids:
            return []
        stmt = select(TCustomer).where(TCustomer.id.in_(ids))
        if not include_deleted:
            stmt = stmt.where(TCustomer.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 树形 =====
    async def list_roots(
        self, *, include_deleted: bool = False
    ) -> list[TCustomer]:
        stmt = select(TCustomer).where(TCustomer.parent_id.is_(None))
        if not include_deleted:
            stmt = stmt.where(TCustomer.deleted_at.is_(None))
        result = await self.session.execute(stmt.order_by(TCustomer.id))
        return list(result.scalars().all())

    async def list_children(
        self, parent_id: int, *, include_deleted: bool = False
    ) -> list[TCustomer]:
        stmt = select(TCustomer).where(TCustomer.parent_id == parent_id)
        if not include_deleted:
            stmt = stmt.where(TCustomer.deleted_at.is_(None))
        result = await self.session.execute(stmt.order_by(TCustomer.id))
        return list(result.scalars().all())

    async def list_all(
        self, *, include_deleted: bool = False
    ) -> list[TCustomer]:
        stmt = select(TCustomer)
        if not include_deleted:
            stmt = stmt.where(TCustomer.deleted_at.is_(None))
        result = await self.session.execute(stmt.order_by(TCustomer.id))
        return list(result.scalars().all())