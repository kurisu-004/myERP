"""ShelfService：货架 CRUD。

软删前校验是否存在 IN_PROCESS / INSPECTION 零件的
`current_holder_id` 指向此货架——若有，拒删（BIZ_SHELF_IN_USE）。
"""
from __future__ import annotations

from fastapi import status as http_status
from sqlalchemy import select

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TPart
from model.enums import PartStatus, ShelfZone
from model.shelf import TShelf
from repository.shelf import ShelfRepository
from repository.user import UserRoleRepository
from schema.shelf import (
    ShelfCreateRequest,
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
        current_user: CurrentUser | None = None,
    ) -> None:
        self.shelves = shelves
        self.user_roles = user_roles
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
                code=s.code,
                name=s.name,
                zone=s.zone,
                location=s.location,
                is_active=s.is_active,
                account_count=account_count_map.get(s.id, 0),
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
            code=s.code,
            name=s.name,
            zone=s.zone,
            location=s.location,
            is_active=s.is_active,
            account_count=account_count,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
