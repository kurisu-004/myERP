"""货架 ↔ 工序 映射业务逻辑层。

核心语义：「整体替换」。
- `list_for_shelf`：取某货架当前映射的全部工序（按 sort_order）。
- `set_for_shelf`：把某货架的映射替换为提交列表（delete-then-insert）。
"""
from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TProcess, TShelfProcess
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from schema.shelf_process import (
    SetShelfProcessRequest,
    ShelfProcessLinkOut,
    ShelfWithProcessesOut,
)
from utils.id_gen import new_id


class ShelfProcessService:
    """货架-工序映射 service。"""

    def __init__(
        self,
        shelves: ShelfRepository,
        processes: ProcessRepository,
        junction: ShelfProcessRepository,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.shelves = shelves
        self.processes = processes
        self.junction = junction
        self._user_id: int | None = current_user.id if current_user else None

    # ===== 查询 =====
    async def list_for_shelf(self, shelf_id: int) -> ShelfWithProcessesOut:
        s = await self.shelves.get_by_id(shelf_id)
        if s is None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_PROCESS_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        rows = await self.junction.list_by_shelf(
            shelf_id, include_deleted=False,
        )
        process_ids = [r.process_id for r in rows]
        process_map: dict[int, TProcess] = {}
        if process_ids:
            procs = await self.processes.list_by_ids(process_ids)
            process_map = {p.id: p for p in procs}
        # 按 sort_order + id 排（list_by_shelf 已排）
        links = [
            ShelfProcessLinkOut(
                process_id=r.process_id,
                process_code=process_map[r.process_id].code
                if r.process_id in process_map
                else "",
                process_name=process_map[r.process_id].name
                if r.process_id in process_map
                else "",
                sort_order=r.sort_order,
            )
            for r in rows
        ]
        return ShelfWithProcessesOut(
            id=s.id,
            code=s.code,
            name=s.name,
            zone=s.zone,
            processes=links,
        )

    # ===== 整体替换 =====
    async def set_for_shelf(
        self, shelf_id: int, data: SetShelfProcessRequest
    ) -> ShelfWithProcessesOut:
        """整体替换某货架的工序映射。

        1. 校验 shelf 存在。
        2. 去重 process_ids（保序）。
        3. 校验所有 process_ids 存在。
        4. 软删该货架的现存映射。
        5. 批量插入新映射（sort_order = 提交顺序 index）。
        """
        s = await self.shelves.get_by_id(shelf_id)
        if s is None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_PROCESS_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 去重保序
        seen: set[int] = set()
        ordered_ids: list[int] = []
        for pid in data.process_ids:
            if pid in seen:
                continue
            seen.add(pid)
            ordered_ids.append(pid)

        # 校验全部 process 存在
        if ordered_ids:
            procs = await self.processes.list_by_ids(ordered_ids)
            found_ids = {p.id for p in procs}
            missing = [pid for pid in ordered_ids if pid not in found_ids]
            if missing:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_PROCESS_PROCESS_NOT_FOUND,
                    message=f"process not found: {missing}",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )

        # 软删现有映射（先给每行赋 updated_by，让 audit 字段一并写入）
        existing = await self.junction.list_by_shelf(
            shelf_id, include_deleted=False,
        )
        for row in existing:
            row.updated_by = self._user_id
        await self.junction.delete_by_shelf(shelf_id)

        # 批量插入新映射
        new_rows: list[TShelfProcess] = []
        for idx, pid in enumerate(ordered_ids):
            new_rows.append(TShelfProcess(
                id=new_id(),
                shelf_id=shelf_id,
                process_id=pid,
                sort_order=idx,
            ))
        for row in new_rows:
            row.created_by = self._user_id
            row.updated_by = self._user_id
            await self.junction.create(row)

        return await self.list_for_shelf(shelf_id)
