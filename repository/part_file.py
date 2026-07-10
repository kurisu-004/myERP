"""零件 / 装配体的统一文件仓储（2026-07-10 起取代 `t_drawing_file` + `t_cnc_program`）。

`part_id` 是 polymorphic owner：
- DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET：`part_id` 是真实 `t_part.id`
- ASSEMBLY_MASTER：`part_id` 是装配体的 id（注意：装配体主图本来也是
  PDF，但通过 `kind='ASSEMBLY_MASTER'` + `part_id=assembly.id` 落到此表，
  与子件 DRAWINGS 完全并列；service/assembly.py 写多页拆分时按此约定）。

约定：
- 所有查询默认 `deleted_at IS NULL`；调用方需要全量时显式
  `include_deleted=True`。
- 单文件 kind（DRAWING / 3D_MODEL / SETUP_SHEET / ASSEMBLY_MASTER）
  在 service 层走「上传前先 soft_delete_by_part_and_kind」；
  G_CODE 允许多版本，**不**调用该方法。
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TPartFile


class PartFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, file: TPartFile) -> TPartFile:
        self.session.add(file)
        await self.session.flush()
        return file

    async def create_many(self, items: list[TPartFile]) -> list[TPartFile]:
        self.session.add_all(items)
        await self.session.flush()
        return items

    # ===== 单条查询 =====
    async def get_by_id(
        self, file_id: int, *, include_deleted: bool = False
    ) -> TPartFile | None:
        f = await self.session.get(TPartFile, file_id)
        if f is None:
            return None
        if not include_deleted and f.deleted_at is not None:
            return None
        return f

    async def get_master_for_assembly(
        self, assembly_id: int, *, include_deleted: bool = False
    ) -> TPartFile | None:
        """取装配体的总装图（kind=ASSEMBLY_MASTER, part_id=assembly.id）。"""
        stmt = select(TPartFile).where(
            and_(
                TPartFile.part_id == assembly_id,
                TPartFile.kind == "ASSEMBLY_MASTER",
            )
        )
        if not include_deleted:
            stmt = stmt.where(TPartFile.deleted_at.is_(None))
        stmt = stmt.order_by(TPartFile.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表查询 =====
    async def list_by_part(
        self,
        part_id: int,
        *,
        kind: str | None = None,
        include_deleted: bool = False,
    ) -> list[TPartFile]:
        """取一个 part / owner 的全部文件（可按 kind 过滤）。"""
        stmt = select(TPartFile).where(TPartFile.part_id == part_id)
        if not include_deleted:
            stmt = stmt.where(TPartFile.deleted_at.is_(None))
        if kind is not None:
            stmt = stmt.where(TPartFile.kind == kind)
        stmt = stmt.order_by(TPartFile.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_owner_and_kinds(
        self,
        owner_id: int,
        kinds: list[str],
        *,
        include_deleted: bool = False,
    ) -> list[TPartFile]:
        """取一个 owner 的多种 kind 文件（装配体详情页用）。"""
        if not kinds:
            return []
        stmt = select(TPartFile).where(
            and_(
                TPartFile.part_id == owner_id,
                TPartFile.kind.in_(kinds),
            )
        )
        if not include_deleted:
            stmt = stmt.where(TPartFile.deleted_at.is_(None))
        stmt = stmt.order_by(TPartFile.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_part_ids(
        self,
        part_ids: list[int],
        *,
        kind: str | None = None,
        include_deleted: bool = False,
    ) -> list[TPartFile]:
        """批量取若干 part 的所有文件（仪表盘批查用）。"""
        if not part_ids:
            return []
        stmt = select(TPartFile).where(TPartFile.part_id.in_(part_ids))
        if not include_deleted:
            stmt = stmt.where(TPartFile.deleted_at.is_(None))
        if kind is not None:
            stmt = stmt.where(TPartFile.kind == kind)
        stmt = stmt.order_by(TPartFile.part_id.asc(), TPartFile.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_owner_many(
        self,
        owner_ids: list[int],
        *,
        kinds: list[str] | None = None,
        include_deleted: bool = False,
    ) -> list[TPartFile]:
        """批量取若干 owner 的所有 / 部分 kind 文件（装配体 list_files 用）。"""
        if not owner_ids:
            return []
        stmt = select(TPartFile).where(TPartFile.part_id.in_(owner_ids))
        if not include_deleted:
            stmt = stmt.where(TPartFile.deleted_at.is_(None))
        if kinds:
            stmt = stmt.where(TPartFile.kind.in_(kinds))
        stmt = stmt.order_by(TPartFile.part_id.asc(), TPartFile.id.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 更新 / 软删 =====
    async def update(self, file: TPartFile) -> TPartFile:
        await self.session.flush()
        return file

    async def soft_delete(self, file: TPartFile) -> TPartFile:
        file.deleted_at = now_naive()
        await self.session.flush()
        return file

    async def soft_delete_many(
        self, files: list[TPartFile]
    ) -> list[str]:
        """批量软删，返回待 COS 清理的 object_key 列表。"""
        if not files:
            return []
        now = now_naive()
        keys: list[str] = []
        for f in files:
            if f.deleted_at is None:
                f.deleted_at = now
                keys.append(f.object_key)
        await self.session.flush()
        return keys

    async def soft_delete_by_part_and_kind(
        self, part_id: int, kind: str
    ) -> list[str]:
        """单文件 kind 的「上传前清理」：返回待 COS 清理的 object_key 列表。"""
        existing = await self.list_by_part(part_id, kind=kind)
        return await self.soft_delete_many(existing)

    # ===== 装配体文件聚合（master + 子件 drawings）=====
    async def list_for_assembly(
        self,
        assembly_id: int,
        *,
        child_part_ids: list[int],
        include_deleted: bool = False,
    ) -> list[TPartFile]:
        """装配体的「总图 + 子件 drawings」聚合查询。

        - 总图：part_id = assembly_id, kind = ASSEMBLY_MASTER
        - 子件 drawings：part_id IN child_part_ids, kind = DRAWING
        """
        if not child_part_ids:
            stmt = select(TPartFile).where(
                and_(
                    TPartFile.part_id == assembly_id,
                    TPartFile.kind == "ASSEMBLY_MASTER",
                )
            )
        else:
            stmt = select(TPartFile).where(
                or_(
                    and_(
                        TPartFile.part_id == assembly_id,
                        TPartFile.kind == "ASSEMBLY_MASTER",
                    ),
                    and_(
                        TPartFile.part_id.in_(child_part_ids),
                        TPartFile.kind == "DRAWING",
                    ),
                )
            )
        if not include_deleted:
            stmt = stmt.where(TPartFile.deleted_at.is_(None))
        # master 排首位（DRAWING 排前），按 id 升序稳定展示
        stmt = stmt.order_by(
            (TPartFile.kind == "ASSEMBLY_MASTER").desc(),
            TPartFile.part_id.asc(),
            TPartFile.id.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())