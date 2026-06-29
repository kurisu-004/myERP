from datetime import datetime

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model import TWorker
from repository.worker import WorkerRepository
from schema.worker import (
    WorkerCreateRequest,
    WorkerListOut,
    WorkerListQuery,
    WorkerOut,
    WorkerUpdateRequest,
)
from utils.id_gen import new_id


class WorkerService:
    """工人管理业务逻辑层。"""

    def __init__(self, workers: WorkerRepository) -> None:
        self.workers = workers

    # ===== 查询 =====
    async def list_workers(self, query: WorkerListQuery) -> WorkerListOut:
        rows = await self.workers.list_with_filters(
            name_like=query.name_like,
            is_active=query.is_active,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.workers.count_with_filters(
            name_like=query.name_like, is_active=query.is_active
        )
        items = [_worker_to_out(w) for w in rows]
        return WorkerListOut(
            items=items, total=total, limit=query.limit, offset=query.offset
        )

    async def get_worker(self, worker_id: int) -> WorkerOut:
        w = await self.workers.get_by_id(worker_id)
        if w is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker {worker_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return _worker_to_out(w)

    # ===== 写 =====
    async def create_worker(self, data: WorkerCreateRequest) -> WorkerOut:
        existing = await self.workers.get_by_badge_code(data.badge_code)
        if existing is not None:
            raise BizError(
                code=ErrCode.CONFLICT,
                message=f"badge_code {data.badge_code!r} already exists",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        w = TWorker(
            id=new_id(),
            badge_code=data.badge_code,
            name=data.name,
            id_card_no=data.id_card_no,
            phone=data.phone,
            is_active=True,
        )
        await self.workers.create(w)
        return _worker_to_out(w)

    async def update_worker(
        self, worker_id: int, data: WorkerUpdateRequest
    ) -> WorkerOut:
        w = await self.workers.get_by_id(worker_id)
        if w is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker {worker_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            w.name = data.name.strip()
        if data.badge_code is not None and data.badge_code != w.badge_code:
            clash = await self.workers.get_by_badge_code(data.badge_code)
            if clash is not None and clash.id != w.id:
                raise BizError(
                    code=ErrCode.CONFLICT,
                    message=f"badge_code {data.badge_code!r} already exists",
                    http_status=http_status.HTTP_409_CONFLICT,
                )
            w.badge_code = data.badge_code
        if data.id_card_no is not None:
            w.id_card_no = data.id_card_no
        if data.phone is not None:
            w.phone = data.phone
        await self.workers.update(w)
        return _worker_to_out(w)

    async def deactivate(self, worker_id: int) -> WorkerOut:
        w = await self.workers.get_by_id(worker_id)
        if w is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker {worker_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        w.is_active = False
        w.deleted_at = datetime.utcnow()
        await self.workers.update(w)
        return _worker_to_out(w)

    async def reactivate(self, worker_id: int) -> WorkerOut:
        w = await self.workers.get_by_id(worker_id, include_deleted=True)
        if w is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker {worker_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        w.is_active = True
        w.deleted_at = None
        await self.workers.update(w)
        return _worker_to_out(w)


def _worker_to_out(w: TWorker) -> WorkerOut:
    return WorkerOut(
        id=w.id,
        badge_code=w.badge_code,
        name=w.name,
        id_card_no=w.id_card_no,
        phone=w.phone,
        is_active=w.is_active,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )