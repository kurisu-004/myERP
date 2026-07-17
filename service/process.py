"""工序 (Process) 业务逻辑层。"""
from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TProcess
from model.enums import ProcessCategory
from repository.process import ProcessRepository
from schema.process import (
    ProcessCreateRequest,
    ProcessListOut,
    ProcessListQuery,
    ProcessOut,
    ProcessUpdateRequest,
)
from utils.id_gen import new_id


class ProcessService:
    """工序管理 service。"""

    def __init__(
        self,
        processes: ProcessRepository,
        junction_repo=None,
        part_repo=None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.processes = processes
        # 可选：用于软删前的引用校验
        self.junction_repo = junction_repo
        self.part_repo = part_repo
        self._user_id: int | None = current_user.id if current_user else None

    # ===== 查询 =====
    async def list_processes(self, query: ProcessListQuery) -> ProcessListOut:
        rows = await self.processes.list_with_filters(
            code_like=query.code_like,
            category=query.category.value if query.category else None,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.processes.count_with_filters(
            code_like=query.code_like,
            category=query.category.value if query.category else None,
        )
        items = [_process_to_out(p) for p in rows]
        return ProcessListOut(
            items=items, total=total, limit=query.limit, offset=query.offset,
        )

    async def get_process(self, process_id: int) -> ProcessOut:
        p = await self.processes.get_by_id(process_id)
        if p is None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {process_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return _process_to_out(p)

    # ===== 写 =====
    async def create_process(self, data: ProcessCreateRequest) -> ProcessOut:
        existing = await self.processes.get_by_code(data.code)
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_DUPLICATE_CODE,
                message=f"process code {data.code!r} already exists",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        p = TProcess(
            id=new_id(),
            code=data.code,
            name=data.name,
            category=data.category.value,
            sort_order=data.sort_order,
            description=data.description,
        )
        p.created_by = self._user_id
        p.updated_by = self._user_id
        await self.processes.create(p)
        return _process_to_out(p)

    async def update_process(
        self, process_id: int, data: ProcessUpdateRequest
    ) -> ProcessOut:
        p = await self.processes.get_by_id(process_id)
        if p is None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {process_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            p.name = data.name.strip()
        if data.category is not None:
            p.category = data.category.value
        if data.sort_order is not None:
            p.sort_order = data.sort_order
        if data.description is not None:
            p.description = data.description
        p.updated_by = self._user_id
        await self.processes.update(p)
        # flush 后 onupdate=func.now() 会让 updated_at 过期；显式 refresh
        # 避免 _process_to_out 同步读 updated_at 触发 MissingGreenlet
        # （与 service/worker.py:159-163 / service/user.py:121-126 同款）
        await self.processes.session.refresh(p)
        return _process_to_out(p)

    async def soft_delete_process(self, process_id: int) -> None:
        p = await self.processes.get_by_id(process_id)
        if p is None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {process_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        await self._assert_not_in_use(process_id)
        p.updated_by = self._user_id
        await self.processes.soft_delete(p)

    async def _assert_not_in_use(self, process_id: int) -> None:
        if self.junction_repo is not None:
            rows = await self.junction_repo.list_by_process(
                process_id, include_deleted=False,
            )
            if rows:
                raise BizError(
                    code=ErrCode.BIZ_PROCESS_IN_USE,
                    message=(
                        f"process {process_id} 仍有 {len(rows)} 条工种映射,"
                        "无法软删"
                    ),
                    http_status=http_status.HTTP_409_CONFLICT,
                )


def _process_to_out(p: TProcess) -> ProcessOut:
    return ProcessOut(
        id=p.id,
        version=p.version,
        code=p.code,
        name=p.name,
        category=ProcessCategory(p.category),
        sort_order=p.sort_order,
        description=p.description,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )