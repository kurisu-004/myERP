"""申请人 (Applicant) 业务逻辑层。

要点：
- 申请人通过 `customer_id` 关联到 **一级客户**（`customer.parent_id IS NULL`），
  service 层强制校验，二级客户不允许挂申请人。
- `get_or_create(name, customer_id)` 给零件/装配体对话框自动新增用：先
  `find_by_name_and_customer` 命中即返回；未命中走 `create`；遇到
  IntegrityError（race）回查一次。
- `soft_delete_applicant` 校验是否被零件的 `applicant_name` 字段引用；
  `PartRepository.count_by_applicant_name_in_customers` 提供计数（含该一级
  客户下所有二级子节点的 customer_id）。

所有 customer_id / applicant_id 入参都是雪花 ID 字符串（CLAUDE.md §3），
service 层用 `parse_snowflake_id` 转回 int 再走 repository。
"""
from __future__ import annotations

from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TApplicant
from repository.applicant import ApplicantRepository
from repository.customer import CustomerRepository
from repository.part import PartRepository
from schema.applicant import (
    ApplicantCreateRequest,
    ApplicantListOut,
    ApplicantListQuery,
    ApplicantOut,
    ApplicantUpdateRequest,
    BulkApplicantItem,
    BulkApplicantOut,
)
from service._id_parse import parse_snowflake_id
from utils.id_gen import new_id


class ApplicantService:
    def __init__(
        self,
        applicants: ApplicantRepository,
        customers: CustomerRepository,
        parts: PartRepository | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.applicants = applicants
        self.customers = customers
        self.parts = parts  # 用于软删前引用校验
        self._user_id: int | None = current_user.id if current_user else None

    # ===== 查询 =====
    async def list_applicants(self, query: ApplicantListQuery) -> ApplicantListOut:
        cid_int = parse_snowflake_id(query.customer_id, field_name="customer_id")
        rows = await self.applicants.list_with_filters(
            customer_id=cid_int,
            name_like=query.name_like,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.applicants.count_with_filters(
            customer_id=cid_int,
            name_like=query.name_like,
        )
        # 预加载客户名（仅一级，减小 SQL）
        items = await self._to_outs(rows)
        return ApplicantListOut(
            items=items, total=total, limit=query.limit, offset=query.offset,
        )

    async def get_applicant(self, applicant_id: str) -> ApplicantOut:
        aid = parse_snowflake_id(applicant_id, field_name="applicant_id")
        if aid is None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                message=f"applicant {applicant_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        a = await self.applicants.get_by_id(aid)
        if a is None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                message=f"applicant {applicant_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        items = await self._to_outs([a])
        return items[0]

    async def search_for_customer(
        self, customer_id: str, name_prefix: str | None, limit: int = 20,
    ) -> list[ApplicantOut]:
        """零件对话框下拉用：限定一级客户范围内前序查询。

        `customer_id` 是雪花 ID 字符串（前端透传，无 JS 精度截断）。
        """
        cid = parse_snowflake_id(customer_id, field_name="customer_id")
        if cid is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 校验一级客户存在
        await self._assert_root_customer(cid)
        rows = await self.applicants.search_by_name_prefix(
            name_prefix=name_prefix or "", customer_id=cid, limit=limit,
        )
        return await self._to_outs(rows)

    # ===== 写 =====
    async def create_applicant(self, data: ApplicantCreateRequest) -> ApplicantOut:
        name = data.name.strip()
        cid = parse_snowflake_id(data.customer_id, field_name="customer_id")
        if cid is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {data.customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        await self._assert_root_customer(cid)
        # 已存在直接报冲突
        existing = await self.applicants.find_by_name_and_customer(
            name=name, customer_id=cid,
        )
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_DUPLICATE_NAME,
                message=(
                    f"客户 {data.customer_id} 下已存在申请人「{name}」"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )
        a = TApplicant(
            id=new_id(), name=name, customer_id=cid,
        )
        a.created_by = self._user_id
        a.updated_by = self._user_id
        await self.applicants.create(a)
        items = await self._to_outs([a])
        return items[0]

    async def update_applicant(
        self, applicant_id: str, data: ApplicantUpdateRequest,
    ) -> ApplicantOut:
        aid = parse_snowflake_id(applicant_id, field_name="applicant_id")
        if aid is None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                message=f"applicant {applicant_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        a = await self.applicants.get_by_id(aid)
        if a is None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                message=f"applicant {applicant_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            a.name = data.name.strip()
        if data.customer_id is not None:
            new_cid = parse_snowflake_id(data.customer_id, field_name="customer_id")
            if new_cid is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {data.customer_id!r} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            await self._assert_root_customer(new_cid)
            a.customer_id = new_cid
        # 若改了 name 或 customer_id，DB 唯一索引可能冲突 → flush 兜底
        try:
            a.updated_by = self._user_id
            await self.applicants.update(a)
        except IntegrityError as e:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_DUPLICATE_NAME,
                message="该客户下已存在同名申请人",
                http_status=http_status.HTTP_409_CONFLICT,
            ) from e
        items = await self._to_outs([a])
        return items[0]

    async def soft_delete_applicant(self, applicant_id: str) -> None:
        aid = parse_snowflake_id(applicant_id, field_name="applicant_id")
        if aid is None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                message=f"applicant {applicant_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        a = await self.applicants.get_by_id(aid)
        if a is None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                message=f"applicant {applicant_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 引用检查：该一级客户（含其下二级客户）下是否有未软删零件
        # 用此申请人姓名。
        await self._assert_not_in_use(a.name, a.customer_id)
        a.updated_by = self._user_id
        await self.applicants.soft_delete(a)

    # ===== 给 PartService / AssemblyService 调用的幂等入口 =====
    async def get_or_create(
        self, name: str, customer_id: int,
    ) -> ApplicantOut:
        """在指定一级客户下找到该姓名的申请人，找不到则创建。

        校验 customer_id 是一级客户；遇 race（IntegrityError）回查一次。

        注：这里 customer_id 入参是 int（已被 PartService / AssemblyService
        在更外层 int() 转过），不需要再 parse_snowflake_id。
        """
        cleaned = name.strip()
        if not cleaned:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="applicant name 不能为空",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        await self._assert_root_customer(customer_id)
        existing = await self.applicants.find_by_name_and_customer(
            name=cleaned, customer_id=customer_id,
        )
        if existing is not None:
            items = await self._to_outs([existing])
            return items[0]
        a = TApplicant(
            id=new_id(), name=cleaned, customer_id=customer_id,
        )
        a.created_by = self._user_id
        a.updated_by = self._user_id
        try:
            await self.applicants.create(a)
        except IntegrityError:
            # 并发 race：另一个事务先插入了同名同客户的申请人 → 回查
            existing = await self.applicants.find_by_name_and_customer(
                name=cleaned, customer_id=customer_id,
            )
            if existing is None:
                raise
            items = await self._to_outs([existing])
            return items[0]
        items = await self._to_outs([a])
        return items[0]

    # ===== 批量 get-or-create（应标 Excel 导入用） =====
    async def bulk_get_or_create(
        self, items: list[BulkApplicantItem],
    ) -> list[BulkApplicantOut]:
        """批量按 (name, customer_id) 找或创建申请人。

        - `customer_id` 允许 L1 / L2；service 内部沿 `parent_id` 上溯到 L1 根
          再走 `get_or_create`（与单条逻辑保持一致）；
        - 内部按 (cleaned_name, l1_root_id) 去重后逐条幂等创建；遇 race
          走 `get_or_create` 自带的 IntegrityError 回查；
        - 返回列表里 `customer_id` 字段统一是 L1 根 id，调用方拿到后
          与自己持有的 L1 根一起即可索引到 `applicant_id`。
        - 入参空列表短路返回 []，不报错。
        """
        if not items:
            return []

        # 解析 + 上溯 L1 根 + dedupe
        seen: dict[tuple[str, int], int] = {}  # (name, l1_root_id) -> l1_root_id
        for item in items:
            cleaned = item.name.strip()
            if not cleaned:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="applicant name 不能为空",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            cid_int = parse_snowflake_id(item.customer_id, field_name="customer_id")
            if cid_int is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {item.customer_id!r} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            cust = await self.customers.get_by_id(cid_int)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {item.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            l1_root_id = cust.parent_id if cust.parent_id is not None else cust.id
            seen[(cleaned, l1_root_id)] = l1_root_id

        # 逐条幂等创建/获取
        results: list[BulkApplicantOut] = []
        for (name, l1_root_id) in seen.keys():
            a = await self.get_or_create(name=name, customer_id=l1_root_id)
            results.append(
                BulkApplicantOut(
                    name=name,
                    customer_id=str(l1_root_id),
                    applicant_id=a.id,
                )
            )
        return results

    # ===== 内部 =====
    async def _assert_root_customer(self, customer_id: int) -> None:
        cust = await self.customers.get_by_id(customer_id)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is not None:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_BAD_CUSTOMER,
                message=(
                    f"customer {customer_id} 不是一级客户（parent_id={cust.parent_id}）"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

    async def _assert_not_in_use(self, name: str, root_customer_id: int) -> None:
        if self.parts is None:
            return
        # 收集一级客户 + 其所有二级子节点的 customer_id
        children = await self.customers.list_children(root_customer_id)
        customer_ids = [root_customer_id] + [c.id for c in children]
        in_use = await self.parts.count_by_applicant_name_in_customers(
            applicant_name=name, customer_ids=customer_ids,
        )
        if in_use > 0:
            raise BizError(
                code=ErrCode.BIZ_APPLICANT_IN_USE,
                message=(
                    f"申请人「{name}」仍被 {in_use} 条未软删零件引用，无法软删"
                ),
                http_status=http_status.HTTP_409_CONFLICT,
            )

    async def _to_outs(self, rows: list[TApplicant]) -> list[ApplicantOut]:
        if not rows:
            return []
        # 一次性拉客户名（一级客户的 id 全部收集）
        customer_ids = list({r.customer_id for r in rows})
        cust_rows = await self.customers.list_by_ids(customer_ids)
        cust_map = {c.id: c.name for c in cust_rows}
        return [
            ApplicantOut(
                id=r.id,
                version=r.version,
                name=r.name,
                customer_id=r.customer_id,
                customer_name=cust_map.get(r.customer_id),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]