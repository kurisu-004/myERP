"""图纸文件仓储。

`t_drawing_file` 的归属要么是 `t_assembly`（装配件总图），要么是
`t_part`（子零件的 STEP/独立图纸）。本仓储的 list 查询按归属分流。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TDrawingFile


class DrawingFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, file: TDrawingFile) -> TDrawingFile:
        self.session.add(file)
        await self.session.flush()
        return file

    async def create_many(self, items: list[TDrawingFile]) -> list[TDrawingFile]:
        self.session.add_all(items)
        await self.session.flush()
        return items

    # ===== 单条查询 =====
    async def get_by_id(
        self, file_id: int, *, include_deleted: bool = False
    ) -> TDrawingFile | None:
        f = await self.session.get(TDrawingFile, file_id)
        if f is None:
            return None
        if not include_deleted and f.deleted_at is not None:
            return None
        return f

    async def get_master_pdf(
        self, assembly_id: int, *, include_deleted: bool = False
    ) -> TDrawingFile | None:
        """取装配件的主 PDF（page_index IS NULL）。"""
        stmt = select(TDrawingFile).where(
            and_(
                TDrawingFile.assembly_id == assembly_id,
                TDrawingFile.file_type == "PDF",
                TDrawingFile.page_index.is_(None),
            )
        )
        if not include_deleted:
            stmt = stmt.where(TDrawingFile.deleted_at.is_(None))
        stmt = stmt.order_by(TDrawingFile.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表查询 =====
    async def list_by_part(
        self,
        part_id: int,
        *,
        file_type: str | None = None,
        include_deleted: bool = False,
    ) -> list[TDrawingFile]:
        stmt = select(TDrawingFile).where(TDrawingFile.part_id == part_id)
        if not include_deleted:
            stmt = stmt.where(TDrawingFile.deleted_at.is_(None))
        if file_type is not None:
            stmt = stmt.where(TDrawingFile.file_type == file_type.upper())
        # 主 PDF 排在前面（page_index=NULL 排首位），其余按 page_index 升序
        stmt = stmt.order_by(
            TDrawingFile.page_index.is_(None).desc(),
            TDrawingFile.page_index.asc(),
            TDrawingFile.id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_assembly(
        self,
        assembly_id: int,
        *,
        file_type: str | None = None,
        include_deleted: bool = False,
    ) -> list[TDrawingFile]:
        """取装配件直接挂的文件（总图 PDF）+ 通过 part_id 挂在其所有子零件上的文件。"""
        from model import TPart

        # 子零件 ids
        child_stmt = select(TPart.id).where(
            and_(
                TPart.assembly_id == assembly_id,
                TPart.deleted_at.is_(None),
            )
        )
        child_ids = [r for r in (await self.session.execute(child_stmt)).scalars()]
        child_id_set: set[int] = set(child_ids)

        # 直接挂装配件的文件 + 挂在子件上的文件
        stmt = select(TDrawingFile).where(
            or_(
                TDrawingFile.assembly_id == assembly_id,
                TDrawingFile.part_id.in_(child_id_set) if child_id_set else False,
            )
        )
        if not include_deleted:
            stmt = stmt.where(TDrawingFile.deleted_at.is_(None))
        if file_type is not None:
            stmt = stmt.where(TDrawingFile.file_type == file_type.upper())
        stmt = stmt.order_by(
            TDrawingFile.page_index.is_(None).desc(),
            TDrawingFile.page_index.asc(),
            TDrawingFile.id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 更新 / 软删 =====
    async def update(self, file: TDrawingFile) -> TDrawingFile:
        await self.session.flush()
        return file

    async def soft_delete(self, file: TDrawingFile) -> TDrawingFile:
        file.deleted_at = datetime.utcnow()
        await self.session.flush()
        return file

    async def soft_delete_many(self, files: list[TDrawingFile]) -> None:
        now = datetime.utcnow()
        for f in files:
            f.deleted_at = now
        await self.session.flush()

    async def list_for_part_ids(
        self, part_ids: list[int], *, include_deleted: bool = False
    ) -> list[TDrawingFile]:
        """批量取若干 part 的所有文件（拼装配件详情用）。"""
        if not part_ids:
            return []
        stmt = select(TDrawingFile).where(TDrawingFile.part_id.in_(part_ids))
        if not include_deleted:
            stmt = stmt.where(TDrawingFile.deleted_at.is_(None))
        stmt = stmt.order_by(
            TDrawingFile.part_id.asc(),
            TDrawingFile.page_index.is_(None).desc(),
            TDrawingFile.page_index.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())