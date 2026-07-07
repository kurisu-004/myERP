"""客户业务逻辑层。"""

from __future__ import annotations

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TCustomer
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.part import PartRepository
from schema.customer import (
    CustomerCreateRequest,
    CustomerOut,
    CustomerUpdateRequest,
)
from service._id_parse import parse_snowflake_id
from utils.id_gen import new_id


class CustomerService:
    def __init__(
        self,
        customers: CustomerRepository,
        parts: PartRepository | None = None,
        assemblies: AssemblyRepository | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.customers = customers
        self.parts = parts  # 用于软删前的引用校验
        self.assemblies = assemblies
        self._user_id: int | None = current_user.id if current_user else None

    # ============================================================
    # 查询
    # ============================================================
    async def list_customers(self) -> list[CustomerOut]:
        """返回所有未软删的客户（含一级 + 二级），含 parent_name。

        用于前端批量导入 Excel 时按 (parent_name, name) 唯一定位叶子客户。
        数据量很小（公司内部客户树 < 100 条），不分页。
        """
        rows = await self.customers.list_all()
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

    async def get_customer(self, customer_id: str) -> CustomerOut:
        # customer_id 入参是雪花 ID 字符串（CLAUDE.md §3），转回 int。
        cid = parse_snowflake_id(customer_id, field_name="customer_id")
        if cid is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        cust = await self.customers.get_by_id(cid)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        parent_name: str | None = None
        if cust.parent_id is not None:
            parent = await self.customers.get_by_id(cust.parent_id)
            parent_name = parent.name if parent else None
        return CustomerOut(
            id=cust.id,
            name=cust.name,
            parent_id=cust.parent_id,
            parent_name=parent_name,
        )

    # ============================================================
    # 写
    # ============================================================
    async def create_customer(self, data: CustomerCreateRequest) -> CustomerOut:
        name = data.name.strip()
        parent_id_str = data.parent_id
        parent_id: int | None = None
        if parent_id_str:
            parent_id = parse_snowflake_id(parent_id_str, field_name="parent_id")
            if parent_id is not None:
                await self._assert_root_parent(parent_id)
        cust = TCustomer(
            id=new_id(), name=name, parent_id=parent_id,
        )
        cust.created_by = self._user_id
        cust.updated_by = self._user_id
        await self.customers.create(cust)
        parent_name = None
        if parent_id is not None:
            parent = await self.customers.get_by_id(parent_id)
            parent_name = parent.name if parent else None
        return CustomerOut(
            id=cust.id, name=cust.name, parent_id=cust.parent_id,
            parent_name=parent_name,
        )

    async def update_customer(
        self, customer_id: str, data: CustomerUpdateRequest,
    ) -> CustomerOut:
        cid = parse_snowflake_id(customer_id, field_name="customer_id")
        if cid is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        cust = await self.customers.get_by_id(cid)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            cust.name = data.name.strip()
        if data.parent_id is not None:
            new_parent_id = parse_snowflake_id(data.parent_id, field_name="parent_id")
            if new_parent_id is None:
                cust.parent_id = None
            else:
                # 防自环
                if new_parent_id == cust.id:
                    raise BizError(
                        code=ErrCode.BIZ_INVALID_VALUE,
                        message="parent_id 不能等于自身 id",
                        http_status=http_status.HTTP_400_BAD_REQUEST,
                    )
                # 若原是 root，新 parent 必须存在且为 root
                await self._assert_root_parent(new_parent_id)
                cust.parent_id = new_parent_id
        cust.updated_by = self._user_id
        await self.customers.update(cust)
        return await self.get_customer(customer_id)

    async def soft_delete_customer(self, customer_id: str) -> None:
        cid = parse_snowflake_id(customer_id, field_name="customer_id")
        if cid is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        cust = await self.customers.get_by_id(cid)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 一级客户：若仍有未软删子 → 拒
        if cust.parent_id is None:
            children = await self.customers.list_children(cid)
            if children:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_IN_USE,
                    message=(
                        f"客户「{cust.name}」仍有 "
                        f"{len(children)} 个二级子节点，无法软删"
                    ),
                    http_status=http_status.HTTP_409_CONFLICT,
                )
        # 任何客户：若被未软删零件 / 装配体引用 → 拒
        if self.parts is not None:
            ref_count = await self.parts.count_with_filters(
                customer_id=cid, include_deleted=False,
            )
            if ref_count > 0:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_IN_USE,
                    message=(
                        f"客户「{cust.name}」仍被 {ref_count} 条零件引用，无法软删"
                    ),
                    http_status=http_status.HTTP_409_CONFLICT,
                )
        if self.assemblies is not None:
            asm_count = await self.assemblies.count_with_filters(
                customer_id=cid, include_deleted=False,
            )
            if asm_count > 0:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_IN_USE,
                    message=(
                        f"客户「{cust.name}」仍被 {asm_count} 条装配体引用，无法软删"
                    ),
                    http_status=http_status.HTTP_409_CONFLICT,
                )
        cust.updated_by = self._user_id
        await self.customers.soft_delete(cust)

    # ===== 内部 =====
    async def _assert_root_parent(self, parent_id: int) -> None:
        parent = await self.customers.get_by_id(parent_id)
        if parent is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"parent customer {parent_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if parent.parent_id is not None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"parent {parent_id} 不是一级客户（仅允许二级挂在一级下）"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )