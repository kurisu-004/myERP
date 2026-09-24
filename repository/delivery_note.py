"""送货单（DeliveryNote）数据访问。

2026-09-24 PR-2：从 git 785df37^ 恢复 ``DeliveryNoteRepository``；
``DeliveryNoteEventRepository`` / ``DeliveryNoteCounterRepository`` 不恢复——
其依赖的 ``TDeliveryNoteEvent`` / ``TDeliveryNoteCounter`` model 计划在 PR-3
清理 dormant model 时一并删除（与对应状态机回调一同下线）。

原 commit `785df37^` 全文保留 ``DeliveryNoteRepository`` 完整实现
（10 个公开方法 + 2 个私有辅助），dormant test
``tests/test_delivery_note_print_merge.py`` 可能直接调用
``list_with_filters`` / ``list_parts`` / ``count_parts`` 等，因此方法签名
不能减。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model.delivery_note import TDeliveryNote
from model.enums import DeliveryNoteSortKey, SortDir
from model.part import TPart


class DeliveryNoteRepository:
    """t_delivery_note 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====

    async def create(self, note: TDeliveryNote) -> TDeliveryNote:
        self.session.add(note)
        await self.session.flush()
        return note

    async def update(self, note: TDeliveryNote) -> TDeliveryNote:
        await self.session.flush()
        return note

    async def soft_delete(self, note: TDeliveryNote) -> TDeliveryNote:
        note.deleted_at = now_naive()
        await self.session.flush()
        return note

    # ===== 单条 =====

    async def get_by_id(
        self,
        note_id: int,
        *,
        include_deleted: bool = False,
    ) -> TDeliveryNote | None:
        result = await self.session.get(TDeliveryNote, note_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_by_no(self, delivery_note_no: str) -> TDeliveryNote | None:
        stmt = (
            select(TDeliveryNote)
            .where(TDeliveryNote.delivery_note_no == delivery_note_no)
            .where(TDeliveryNote.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_ids(
        self,
        ids: list[int],
        *,
        include_deleted: bool = False,
    ) -> list[TDeliveryNote]:
        if not ids:
            return []
        stmt = select(TDeliveryNote).where(TDeliveryNote.id.in_(ids))
        if not include_deleted:
            stmt = stmt.where(TDeliveryNote.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 一览 (clerk/manager) =====

    async def list_with_filters(
        self,
        *,
        statuses: list[str] | None = None,
        customer_id: int | None = None,
        keyword: str | None = None,
        sort_by: DeliveryNoteSortKey = DeliveryNoteSortKey.CREATED_AT,
        sort_dir: SortDir = SortDir.DESC,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TDeliveryNote]:
        stmt = self._build_filter_stmt(
            statuses=statuses,
            customer_id=customer_id,
            keyword=keyword,
        )
        stmt = stmt.order_by(*self._order_cols(sort_by, sort_dir))
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        statuses: list[str] | None = None,
        customer_id: int | None = None,
        keyword: str | None = None,
    ) -> int:
        stmt = self._build_filter_stmt(
            statuses=statuses,
            customer_id=customer_id,
            keyword=keyword,
        ).with_only_columns(func.count(TDeliveryNote.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 待送货一览 (司机扫码台 /scan/delivery-note-pickup) =====

    async def list_for_pickup(
        self,
        *,
        customer_id: int | None = None,
    ) -> list[TDeliveryNote]:
        """仅取 SUBMITTED 状态的非软删单，按 submitted_at DESC 排序。"""
        from model.enums import DeliveryNoteStatus

        stmt = (
            select(TDeliveryNote)
            .where(TDeliveryNote.deleted_at.is_(None))
            .where(TDeliveryNote.status == DeliveryNoteStatus.SUBMITTED.value)
        )
        if customer_id is not None:
            stmt = stmt.where(TDeliveryNote.customer_id == customer_id)
        stmt = stmt.order_by(TDeliveryNote.submitted_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 零件关联 =====

    async def list_parts(self, note_id: int) -> list[TPart]:
        """返回该送货单下所有未软删的 part（按 serial_no ASC）。"""
        stmt = (
            select(TPart)
            .where(TPart.delivery_note_id == note_id)
            .where(TPart.deleted_at.is_(None))
            .order_by(TPart.serial_no.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_parts(self, note_id: int) -> int:
        stmt = (
            select(func.count(TPart.id))
            .where(TPart.delivery_note_id == note_id)
            .where(TPart.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== filter builder + 排序列 =====

    def _build_filter_stmt(
        self,
        *,
        statuses: list[str] | None,
        customer_id: int | None,
        keyword: str | None,
    ):
        stmt = select(TDeliveryNote).where(TDeliveryNote.deleted_at.is_(None))
        if statuses:
            if len(statuses) == 1:
                stmt = stmt.where(TDeliveryNote.status == statuses[0])
            else:
                stmt = stmt.where(TDeliveryNote.status.in_(statuses))
        if customer_id is not None:
            stmt = stmt.where(TDeliveryNote.customer_id == customer_id)
        if keyword:
            kw = f"%{keyword.strip()}%"
            stmt = stmt.where(TDeliveryNote.delivery_note_no.ilike(kw))
        return stmt

    def _order_cols(
        self,
        sort_by: DeliveryNoteSortKey,
        sort_dir: SortDir,
    ):
        col_map = {
            DeliveryNoteSortKey.CREATED_AT: TDeliveryNote.created_at,
            DeliveryNoteSortKey.SUBMITTED_AT: TDeliveryNote.submitted_at,
            DeliveryNoteSortKey.PICKED_UP_AT: TDeliveryNote.picked_up_at,
            DeliveryNoteSortKey.DELIVERY_NOTE_NO: TDeliveryNote.delivery_note_no,
        }
        col = col_map.get(sort_by, TDeliveryNote.created_at)
        if sort_dir == SortDir.ASC:
            return (col.asc(), TDeliveryNote.id.asc())
        return (col.desc(), TDeliveryNote.id.desc())
