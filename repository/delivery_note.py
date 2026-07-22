"""送货单（DeliveryNote）数据访问（2026-07-22 新增）.

含 `DeliveryNoteRepository`（送货单主表）与
`DeliveryNoteCounterRepository`（单号每日计数器）。
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from fastapi import status as http_status
from model.delivery_note import TDeliveryNote
from model.delivery_note_counter import TDeliveryNoteCounter
from model.delivery_note_event import TDeliveryNoteEvent
from model.enums import DeliveryNoteSortKey, DeliveryNoteStatus, SortDir
from model.part import TPart

if TYPE_CHECKING:
    pass


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
            statuses=statuses, customer_id=customer_id, keyword=keyword,
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
            statuses=statuses, customer_id=customer_id, keyword=keyword,
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
        stmt = (
            select(TDeliveryNote)
            .where(TDeliveryNote.deleted_at.is_(None))
            .where(
                TDeliveryNote.status == DeliveryNoteStatus.SUBMITTED.value
            )
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
        stmt = select(TDeliveryNote).where(
            TDeliveryNote.deleted_at.is_(None)
        )
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


class DeliveryNoteEventRepository:
    """t_delivery_note_event 数据访问（append-only）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, event: TDeliveryNoteEvent) -> TDeliveryNoteEvent:
        """同步 add（state machine callback 用），不 flush。"""
        self.session.add(event)
        return event

    async def create(self, event: TDeliveryNoteEvent) -> TDeliveryNoteEvent:
        """显式 flush（service 直接构造 TDeliveryNoteEvent 时）。"""
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_by_note(
        self, note_id: int,
    ) -> list[TDeliveryNoteEvent]:
        stmt = (
            select(TDeliveryNoteEvent)
            .where(TDeliveryNoteEvent.delivery_note_id == note_id)
            .order_by(TDeliveryNoteEvent.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class DeliveryNoteCounterRepository:
    """t_delivery_note_counter 数据访问；acquire_no() 用 INSERT ON CONFLICT
    原子递增（PG row-level lock 自然并发安全）。

    使用方式::
        n = await counter_repo.acquire_no("20260723")
        note_no = f"DN-20260723-{n:04d}"   # 例：DN-20260723-0001
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire_no(self, today: str) -> int:
        """原子发放当日下一个 NN（自增 1 后返回新值）。

        Args:
            today: YYYYMMDD 字符串（service 层 `core.time.today_yyyymmdd()` 取）。

        Returns:
            自增后的 NN（≥1）。
        """
        stmt = (
            pg_insert(TDeliveryNoteCounter)
            .values(date_ymd=today, last_value=1)
            .on_conflict_do_update(
                index_elements=["date_ymd"],
                set_=dict(
                    last_value=TDeliveryNoteCounter.last_value + 1,
                    updated_at=func.now(),
                ),
            )
            .returning(TDeliveryNoteCounter.last_value)
        )
        result = await self.session.execute(stmt)
        row = result.fetchone()
        if row is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="failed to acquire delivery_note counter",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return int(row[0])
