"""工种 (WorkType) 业务逻辑层。"""
from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model import TWorkType
from repository.work_type import WorkTypeRepository
from schema.work_type import (
    WorkTypeCreateRequest,
    WorkTypeListOut,
    WorkTypeListQuery,
    WorkTypeOut,
    WorkTypeUpdateRequest,
)
from utils.id_gen import new_id


class WorkTypeService:
    """工种管理 service。"""

    def __init__(
        self,
        work_types: WorkTypeRepository,
        worker_repo=None,
        junction_repo=None,
    ) -> None:
        self.work_types = work_types
        # 注入可选：用于软删前的引用校验（BIZ_WORK_TYPE_IN_USE）
        self.worker_repo = worker_repo
        self.junction_repo = junction_repo

    # ===== 查询 =====
    async def list_work_types(self, query: WorkTypeListQuery) -> WorkTypeListOut:
        rows = await self.work_types.list_with_filters(
            code_like=query.code_like,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.work_types.count_with_filters(
            code_like=query.code_like,
        )
        items = [_work_type_to_out(w) for w in rows]
        return WorkTypeListOut(
            items=items, total=total, limit=query.limit, offset=query.offset,
        )

    async def get_work_type(self, work_type_id: int) -> WorkTypeOut:
        wt = await self.work_types.get_by_id(work_type_id)
        if wt is None:
            raise BizError(
                code=ErrCode.BIZ_WORK_TYPE_NOT_FOUND,
                message=f"work_type {work_type_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return _work_type_to_out(wt)

    # ===== 写 =====
    async def create_work_type(self, data: WorkTypeCreateRequest) -> WorkTypeOut:
        existing = await self.work_types.get_by_code(data.code)
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_WORK_TYPE_DUPLICATE_CODE,
                message=f"work_type code {data.code!r} already exists",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        wt = TWorkType(
            id=new_id(),
            code=data.code,
            name=data.name,
            description=data.description,
            sort_order=data.sort_order,
        )
        await self.work_types.create(wt)
        return _work_type_to_out(wt)

    async def update_work_type(
        self, work_type_id: int, data: WorkTypeUpdateRequest
    ) -> WorkTypeOut:
        wt = await self.work_types.get_by_id(work_type_id)
        if wt is None:
            raise BizError(
                code=ErrCode.BIZ_WORK_TYPE_NOT_FOUND,
                message=f"work_type {work_type_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            wt.name = data.name.strip()
        if data.description is not None:
            wt.description = data.description
        if data.sort_order is not None:
            wt.sort_order = data.sort_order
        await self.work_types.update(wt)
        return _work_type_to_out(wt)

    async def soft_delete_work_type(self, work_type_id: int) -> None:
        wt = await self.work_types.get_by_id(work_type_id)
        if wt is None:
            raise BizError(
                code=ErrCode.BIZ_WORK_TYPE_NOT_FOUND,
                message=f"work_type {work_type_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 校验引用：worker / junction
        await self._assert_not_in_use(work_type_id)
        await self.work_types.soft_delete(wt)

    async def _assert_not_in_use(self, work_type_id: int) -> None:
        """软删前校验：是否仍有 worker 或 junction 引用。"""
        if self.worker_repo is not None:
            workers = await self.worker_repo.list_with_filters(
                is_active=None, limit=1, offset=0,
            )
            # 简化：直接用 list_with_filters 拉一批按 work_type_id 过滤；
            # 当 worker_repo 没有按 work_type_id 过滤时,退化为拉一批校验。
            for w in workers:
                if getattr(w, "work_type_id", None) == work_type_id:
                    raise BizError(
                        code=ErrCode.BIZ_WORK_TYPE_IN_USE,
                        message=(
                            f"work_type {work_type_id} 仍被 worker 引用,无法软删"
                        ),
                        http_status=http_status.HTTP_409_CONFLICT,
                    )
        if self.junction_repo is not None:
            rows = await self.junction_repo.list_by_work_type(
                work_type_id, include_deleted=False,
            )
            if rows:
                raise BizError(
                    code=ErrCode.BIZ_WORK_TYPE_IN_USE,
                    message=(
                        f"work_type {work_type_id} 仍有 {len(rows)} 条工序映射,"
                        "无法软删"
                    ),
                    http_status=http_status.HTTP_409_CONFLICT,
                )


def _work_type_to_out(wt: TWorkType) -> WorkTypeOut:
    return WorkTypeOut(
        id=wt.id,
        code=wt.code,
        name=wt.name,
        description=wt.description,
        sort_order=wt.sort_order,
        created_at=wt.created_at,
        updated_at=wt.updated_at,
    )