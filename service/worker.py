from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from core.time import now_naive
from model import TWorker
from repository.work_type import WorkTypeRepository
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

    def __init__(
        self,
        workers: WorkerRepository,
        work_types: WorkTypeRepository | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.workers = workers
        self.work_types = work_types
        self._user_id: int | None = current_user.id if current_user else None

    async def _resolve_work_type(self, work_type_id: int | None) -> int | None:
        if work_type_id is None:
            return None
        if self.work_types is None:
            # 若 service 未注入 work_types repo，假定调用方已在外层校验
            return work_type_id
        wt = await self.work_types.get_by_id(work_type_id)
        if wt is None:
            raise BizError(
                code=ErrCode.BIZ_WORK_TYPE_NOT_FOUND,
                message=f"work_type {work_type_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return wt.id

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

    async def verify_badge(self, badge_code: str) -> WorkerOut:
        """扫码台按工牌码定位工人（单点查询，不返列表）。

        - 不存在 → 404 BIZ_WORKER_NOT_FOUND
        - 已停用 → 400 BIZ_WORKER_INACTIVE
        - 命中且在职 → 返回完整 WorkerOut

        复用 repository.get_by_badge_code（默认过滤 deleted_at IS NULL）。
        与 get_worker 行为对齐：deleted 或未命中都按 404 处理。
        入参自动 strip：API 层 schema 已经 strip，service 再做一次防御。
        """
        code = badge_code.strip()
        if not code:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message="badge_code is empty",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        w = await self.workers.get_by_badge_code(code)
        if w is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"badge_code {code!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not w.is_active:
            raise BizError(
                code=ErrCode.BIZ_WORKER_INACTIVE,
                message=f"worker {w.name} (badge {w.badge_code!r}) is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
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
        wt_id = await self._resolve_work_type(data.work_type_id)
        w = TWorker(
            id=new_id(),
            badge_code=data.badge_code,
            name=data.name,
            id_card_no=data.id_card_no,
            phone=data.phone,
            work_type_id=wt_id,
            is_active=True,
        )
        w.created_by = self._user_id
        w.updated_by = self._user_id
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
        if data.work_type_id is not None:
            w.work_type_id = await self._resolve_work_type(data.work_type_id)
        w.updated_by = self._user_id
        await self.workers.update(w)
        # flush 后 onupdate=func.now() 会让 updated_at 过期；显式 refresh
        await self.workers.session.refresh(w)
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
        w.deleted_at = now_naive()
        w.updated_by = self._user_id
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
        w.updated_by = self._user_id
        await self.workers.update(w)
        return _worker_to_out(w)


def _worker_to_out(w: TWorker) -> WorkerOut:
    return WorkerOut(
        id=w.id,
        badge_code=w.badge_code,
        name=w.name,
        id_card_no=w.id_card_no,
        phone=w.phone,
        work_type_id=w.work_type_id,
        is_active=w.is_active,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )