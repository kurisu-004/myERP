"""客户业务逻辑层。"""

from __future__ import annotations

from repository.customer import CustomerRepository
from schema.customer import CustomerOut


class CustomerService:
    def __init__(self, customers: CustomerRepository) -> None:
        self.customers = customers

    async def list_customers(self) -> list[CustomerOut]:
        """返回所有未软删的客户（含一级 + 二级），含 parent_name。

        用于前端批量导入 Excel 时按 (parent_name, name) 唯一定位叶子客户。
        数据量很小（公司内部客户树 < 100 条），不分页。
        """
        rows = await self.customers.list_all()
        # 一次性把 parent 全部查出来，再组装 parent_name
        parent_ids = [c.parent_id for c in rows if c.parent_id is not None]
        parents: dict[int, str] = {}
        if parent_ids:
            parent_rows = await self.customers.list_by_ids(parent_ids)
            parents = {p.id: p.name for p in parent_rows}
        return [
            CustomerOut(
                id=c.id,
                name=c.name,
                parent_id=c.parent_id,
                parent_name=parents.get(c.parent_id) if c.parent_id else None,
            )
            for c in rows
        ]