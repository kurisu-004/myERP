"""CNC 程序文件仓储。

`t_cnc_program` 只归属到 `t_part`（一个零件可有多份 G 代码版本）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TCncProgram


class CncProgramRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, file: TCncProgram) -> TCncProgram:
        self.session.add(file)
        await self.session.flush()
        return file

    async def get_by_id(
        self, file_id: int, *, include_deleted: bool = False
    ) -> TCncProgram | None:
        f = await self.session.get(TCncProgram, file_id)
        if f is None:
            return None
        if not include_deleted and f.deleted_at is not None:
            return None
        return f

    async def list_by_part(
        self, part_id: int, *, include_deleted: bool = False
    ) -> list[TCncProgram]:
        stmt = select(TCncProgram).where(TCncProgram.part_id == part_id)
        if not include_deleted:
            stmt = stmt.where(TCncProgram.deleted_at.is_(None))
        stmt = stmt.order_by(TCncProgram.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, file: TCncProgram) -> TCncProgram:
        file.deleted_at = datetime.utcnow()
        await self.session.flush()
        return file
