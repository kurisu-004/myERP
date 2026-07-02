"""菜单 / 角色↔菜单 数据访问。"""
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TMenu, TRoleMenu


@dataclass(frozen=True)
class MenuRow:
    """`MenuRepository.list_active_for_roles` 的扁平返回行。

    service 层负责组装成树；repository 只负责把行拉回来。
    """

    id: int
    parent_id: int | None
    code: str
    title: str
    path: str | None
    icon: str | None
    sort_order: int


class MenuRepository:
    """t_menu + t_role_menu 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active_for_roles(
        self, roles: Sequence[str]
    ) -> list[MenuRow]:
        """返回 `roles` 任意一个角色可见的、is_active=true、未软删的所有菜单行（拍平）。

        排序：parent_id NULLS FIRST（顶层在前），同 parent 下按 sort_order，
        再按 id 保稳定序。service 层负责组树。
        """
        if not roles:
            return []
        stmt = (
            select(
                TMenu.id,
                TMenu.parent_id,
                TMenu.code,
                TMenu.title,
                TMenu.path,
                TMenu.icon,
                TMenu.sort_order,
            )
            .join(TRoleMenu, TRoleMenu.menu_id == TMenu.id)
            .where(
                TMenu.is_active.is_(True),
                TMenu.deleted_at.is_(None),
                TRoleMenu.role.in_(list(roles)),
                TRoleMenu.deleted_at.is_(None),
            )
            .order_by(
                TMenu.parent_id.nulls_first(),
                TMenu.sort_order,
                TMenu.id,
            )
        )
        result = await self.session.execute(stmt)
        return [
            MenuRow(
                id=int(r[0]),
                parent_id=r[1],
                code=r[2],
                title=r[3],
                path=r[4],
                icon=r[5],
                sort_order=int(r[6]),
            )
            for r in result.all()
        ]