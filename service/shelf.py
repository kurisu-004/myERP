"""ShelfService：货架 CRUD。

软删前校验是否存在 IN_PROCESS / INSPECTION 零件的
`current_holder_id` 指向此货架——若有，拒删（BIZ_SHELF_IN_USE）。

2026-07-10 共享 HMI 多货架改造（plan: glowing-giggling-liskov.md）：
- `display_order` 字段写入 / 更新（Manager 后台手填）
- `list_for_return(next_process_id)`：RETURN 流程卡片网格 picker 数据源
  候选架 = active PRODUCTION ∩ 映射了 next_process_id；
  按 current_load ASC 排序，top-1 标记 is_recommended
- 新依赖：`parts` / `processes` / `shelf_process` 三个 repository
  （list_for_return 用，CRUD 流不依赖——可选参数避免拖垮已有测试）
"""
from __future__ import annotations

from fastapi import status as http_status
from sqlalchemy import select

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TPart
from model.enums import PartStatus, ShelfZone, UserRole
from model.shelf import TShelf
from repository.part import PartRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.user import UserRoleRepository
from repository.process import ProcessRepository
from schema.shelf import (
    ShelfCreateRequest,
    ShelfForReturnListOut,
    ShelfForReturnOut,
    ShelfListOut,
    ShelfListQuery,
    ShelfOut,
    ShelfUpdateRequest,
)
from utils.id_gen import new_id


class ShelfService:
    def __init__(
        self,
        shelves: ShelfRepository,
        user_roles: UserRoleRepository,
        *,
        parts: PartRepository | None = None,
        processes: ProcessRepository | None = None,
        shelf_process: ShelfProcessRepository | None = None,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.shelves = shelves
        self.user_roles = user_roles
        # 可选 deps：list_for_return 必用，CRUD 流可省略（兼容旧测试）
        self.parts = parts
        self.processes = processes
        self.shelf_process = shelf_process
        self._user_id: int | None = current_user.id if current_user else None

    async def list_shelves(self, query: ShelfListQuery) -> ShelfListOut:
        rows = await self.shelves.list_with_filters(
            zone=query.zone.value if query.zone else None,
            is_active=query.is_active,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.shelves.count_with_filters(
            zone=query.zone.value if query.zone else None,
            is_active=query.is_active,
        )
        # 批量补 account_count
        ids = [s.id for s in rows]
        account_count_map = await self._account_count_map(ids)
        items = [
            ShelfOut(
                id=s.id,
                version=s.version,
                code=s.code,
                name=s.name,
                zone=s.zone,
                location=s.location,
                is_active=s.is_active,
                account_count=account_count_map.get(s.id, 0),
                display_order=s.display_order,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in rows
        ]
        return ShelfListOut(
            items=items, total=total, limit=query.limit, offset=query.offset
        )

    async def get_shelf(self, shelf_id: int) -> ShelfOut:
        s = await self.shelves.get_by_id(shelf_id)
        if s is None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        account_count_map = await self._account_count_map([shelf_id])
        return await self._to_out(s, account_count_map.get(s.id, 0))

    async def create_shelf(self, data: ShelfCreateRequest) -> ShelfOut:
        zone = data.zone.value if isinstance(data.zone, ShelfZone) else data.zone
        if zone not in (ShelfZone.PRODUCTION.value, ShelfZone.INSPECTION.value):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"invalid zone {zone!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        existing = await self.shelves.get_by_code(data.code.strip())
        if existing is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_DUPLICATE_CODE,
                message=f"shelf code {data.code!r} already exists",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        s = TShelf(
            id=new_id(),
            code=data.code.strip(),
            name=data.name.strip(),
            zone=zone,
            location=(data.location or "").strip() or None,
            is_active=True,
            display_order=data.display_order,
        )
        s.created_by = self._user_id
        s.updated_by = self._user_id
        await self.shelves.create(s)
        return await self._to_out(s, 0)

    async def update_shelf(
        self, shelf_id: int, data: ShelfUpdateRequest
    ) -> ShelfOut:
        s = await self.shelves.get_by_id(shelf_id)
        if s is None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            s.name = data.name.strip()
        if data.location is not None:
            s.location = data.location.strip() or None
        if data.is_active is not None:
            s.is_active = data.is_active
        if data.display_order is not None:
            s.display_order = data.display_order
        s.updated_by = self._user_id
        await self.shelves.update(s)
        await self._refresh(s)
        account_count_map = await self._account_count_map([s.id])
        return await self._to_out(s, account_count_map.get(s.id, 0))

    async def soft_delete_shelf(self, shelf_id: int) -> ShelfOut:
        s = await self.shelves.get_by_id(shelf_id)
        if s is None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        # 校验：还有零件 holder 指向此 shelf 且状态非终态？
        # 注意：holder 是多态的；按 shelf.id 直接 in_() 即可（不可能等于 worker.id）。
        from core.database import SessionLocal

        async with SessionLocal() as s2:
            stmt = select(TPart.id).where(
                TPart.current_holder_id == s.id,
                TPart.deleted_at.is_(None),
                TPart.status.in_(
                    [
                        PartStatus.IN_PROCESS.value,
                        PartStatus.INSPECTION.value,
                        PartStatus.REPAIRING.value,
                    ]
                ),
            ).limit(1)
            found = (await s2.execute(stmt)).scalar_one_or_none()
        if found is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf {s.code!r} still holds active parts; cannot soft-delete",
                http_status=http_status.HTTP_409_CONFLICT,
            )
        s.updated_by = self._user_id
        await self.shelves.soft_delete(s)
        await self._refresh(s)
        account_count_map = await self._account_count_map([s.id])
        return await self._to_out(s, account_count_map.get(s.id, 0), include_deleted=True)

    # ============================================================
    # 共享 HMI：RETURN 卡片网格 picker 数据源
    # ============================================================
    async def list_for_return(
        self, next_process_id: int, *,
        user: CurrentUser | None = None,
    ) -> ShelfForReturnListOut:
        """列出 RETURN 流程可选的 PRODUCTION 货架 + 系统推荐。

        候选条件：
        1. `zone == PRODUCTION` 且 `is_active=True` 且未软删
        2. `next_process_id ∈ shelf.assigned_processes`（即映射的工序 code 与
           next_process.code 一致）
        3. 2026-07-13 起：scoped SHELF_ACCOUNT 仅看到自己绑定的架
           （MANAGER / wildcard SHELF_ACCOUNT 不收口）

        排序：`current_load ASC, display_order ASC, code ASC`（最空的优先）。
        标记：top-1 标 `is_recommended=True` 并写入 `recommended_shelf_id`，
        前端 picker 默认高亮 + 「完成」一键接受。

        错误：没有候选架 → `BIZ_SHELF_NO_MATCH_FOR_PROCESS 400`（明确告诉
        工人/经理要先去 ShelfList 给某架配这个工序，或该账号没绑对应架）。
        """
        if self.processes is None or self.parts is None or self.shelf_process is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    "list_for_return requires parts/processes/shelf_process repos; "
                    "service not configured for HMI picker"
                ),
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        # 1) 校验 next_process_id
        process = await self.processes.get_by_id(next_process_id)
        if process is None or process.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {next_process_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        target_code = process.code

        # 2) 拿所有 active PRODUCTION 架（已按 display_order, code 排好）
        all_shelves = await self.shelves.list_active_production_ordered()
        if not all_shelves:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NO_MATCH_FOR_PROCESS,
                message=(
                    f"no active PRODUCTION shelf exists; "
                    f"cannot return to process {target_code!r}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        shelf_ids = [s.id for s in all_shelves]

        # 3) 批量拿 load + mapped process codes（避免 N+1）
        load_map = await self.parts.get_load_map_by_shelf_ids(shelf_ids)
        codes_map = (
            await self.shelf_process.list_mapped_process_codes_by_shelf_ids(shelf_ids)
        )

        # 4) 过滤：mapped_process_codes 包含 target_code
        candidates: list[ShelfForReturnOut] = []
        for s in all_shelves:
            codes = codes_map.get(s.id, [])
            if target_code not in codes:
                continue
            candidates.append(
                ShelfForReturnOut(
                    id=str(s.id),
                    code=s.code,
                    name=s.name,
                    location=s.location,
                    display_order=s.display_order,
                    current_load=load_map.get(s.id, 0),
                    mapped_process_codes=codes,
                    is_recommended=False,
                )
            )

        # 5) 2026-07-13 user scope 收口：scoped SHELF_ACCOUNT 只能选绑定的架
        if user is not None and not user.has_role(UserRole.MANAGER) and not user.shelf_wildcard:
            if not user.has_role(UserRole.SHELF_ACCOUNT):
                # 非 HMI 角色（CLERK / INSPECTOR / CNC）调用 picker → 空
                candidates = []
            else:
                allowed = set(user.shelf_ids)
                candidates = [c for c in candidates if int(c.id) in allowed]

        if not candidates:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NO_MATCH_FOR_PROCESS,
                message=(
                    f"no active shelf mapped to process {target_code!r} "
                    f"in current user's scope; "
                    "configure shelf→process mapping in /shelves/{id}/processes, "
                    "or bind more shelves to this SHELF_ACCOUNT user"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 6) 排序：current_load ASC, display_order ASC, code ASC
        candidates.sort(
            key=lambda x: (x.current_load, x.display_order, x.code)
        )

        # 7) 标推荐
        candidates[0].is_recommended = True

        return ShelfForReturnListOut(
            items=candidates,
            recommended_shelf_id=str(candidates[0].id),
        )

    # ============================================================
    # 共享 HMI：INSPECT 卡片网格 picker 数据源（2026-07-13）
    # ============================================================
    async def list_for_inspection(
        self, *, user: CurrentUser | None = None,
    ) -> ShelfForReturnListOut:
        """列出 INSPECT 流程可选的 INSPECTION 货架 + 系统推荐。

        候选条件：
        1. `zone == INSPECTION` 且 `is_active=True` 且未软删
        2. scoped SHELF_ACCOUNT 仅看到自己绑定的品检架
           （MANAGER / wildcard SHELF_ACCOUNT 不收口）

        排序 + 推荐标记同 `list_for_return`。

        错误：没有候选架 → `BIZ_SHELF_NO_MATCH_FOR_PROCESS 400`。
        """
        if self.parts is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    "list_for_inspection requires parts repo; "
                    "service not configured for HMI picker"
                ),
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        rows = await self.shelves.list_active_by_zone(ShelfZone.INSPECTION.value)
        if not rows:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NO_MATCH_FOR_PROCESS,
                message="no active INSPECTION shelf exists",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 2026-07-13 user scope 收口（同 list_for_return）
        if user is not None and not user.has_role(UserRole.MANAGER) and not user.shelf_wildcard:
            if not user.has_role(UserRole.SHELF_ACCOUNT):
                rows = []
            else:
                allowed = set(user.shelf_ids)
                rows = [s for s in rows if s.id in allowed]

        if not rows:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NO_MATCH_FOR_PROCESS,
                message=(
                    "no INSPECTION shelf in current user's scope; "
                    "bind an INSPECTION shelf to this SHELF_ACCOUNT user"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # load map（不查 mapped_process_codes：品检架无 process 概念）
        load_map = await self.parts.get_load_map_by_shelf_ids([s.id for s in rows])
        items: list[ShelfForReturnOut] = [
            ShelfForReturnOut(
                id=str(s.id),
                code=s.code,
                name=s.name,
                location=s.location,
                display_order=s.display_order,
                current_load=load_map.get(s.id, 0),
                mapped_process_codes=[],
                is_recommended=False,
            )
            for s in rows
        ]
        items.sort(key=lambda x: (x.current_load, x.display_order, x.code))
        if items:
            items[0].is_recommended = True
        return ShelfForReturnListOut(
            items=items,
            recommended_shelf_id=str(items[0].id) if items else "",
        )

    # ============================================================
    # 批量映射（2026-07-17）
    # ============================================================
    async def list_all_process_mappings(self) -> "ShelfProcessMappingsOut":
        """一次性返回所有 active 货架的工序 id 列表，给前端 composable 消费。

        比 N 次 `GET /shelves/{id}/processes` 节省 (N-1) 次 RTT；
        同时也避免 worker 扫码台反复遍历 mapping。空映射的货架不出现。
        """
        from schema.shelf import ShelfProcessMappingItem, ShelfProcessMappingsOut

        if self.shelf_process is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    "list_all_process_mappings requires shelf_process repo; "
                    "service not configured"
                ),
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        mapping = await self.shelf_process.list_all_mappings()
        items = [
            ShelfProcessMappingItem(
                shelf_id=str(sid),
                process_ids=[str(pid) for pid in pids],
            )
            for sid, pids in mapping.items()
        ]
        return ShelfProcessMappingsOut(items=items)

    # ============================================================
    # 内部
    # ============================================================
    async def _refresh(self, s: TShelf) -> None:
        """刷新 ORM 对象，防止 _account_count_map 开新 session 导致过期。

        测试环境 mock 不含 session，用 getattr 做兼容。
        """
        session = getattr(self.shelves, 'session', None)
        if session is not None:
            await session.refresh(s)


    async def _account_count_map(self, shelf_ids: list[int]) -> dict[int, int]:
        if not shelf_ids:
            return {}
        out: dict[int, int] = {sid: 0 for sid in shelf_ids}
        roles = []
        for sid in shelf_ids:
            # 直接通过 role 表查询；为节省一次会话，串行调用即可
            pass
        # 一次 SQL 取每个 shelf 上的 role 数
        from core.database import SessionLocal
        from sqlalchemy import func

        from model import TUserRole

        async with SessionLocal() as s2:
            stmt = (
                select(TUserRole.scope_id, func.count(TUserRole.id))
                .where(
                    TUserRole.scope_type == "shelf",
                    TUserRole.scope_id.in_(shelf_ids),
                    TUserRole.deleted_at.is_(None),
                )
                .group_by(TUserRole.scope_id)
            )
            rows = (await s2.execute(stmt)).all()
        for sid, n in rows:
            out[int(sid)] = int(n)
        return out

    async def _to_out(
        self,
        s: TShelf,
        account_count: int,
        *,
        include_deleted: bool = False,
    ) -> ShelfOut:
        return ShelfOut(
            id=s.id,
            version=s.version,
            code=s.code,
            name=s.name,
            zone=s.zone,
            location=s.location,
            is_active=s.is_active,
            account_count=account_count,
            display_order=s.display_order,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
