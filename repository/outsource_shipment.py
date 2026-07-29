"""外协发货记录 (t_outsource_shipment) 数据访问。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time import now_naive
from model import TOutsourceShipment
from model.enums import OutsourceSentPartSortKey, SortDir


class OutsourceShipmentRepository:
    """t_outsource_shipment 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, shipment: TOutsourceShipment) -> TOutsourceShipment:
        self.session.add(shipment)
        await self.session.flush()
        return shipment

    async def update(self, shipment: TOutsourceShipment) -> TOutsourceShipment:
        await self.session.flush()
        return shipment

    async def soft_delete(self, shipment: TOutsourceShipment) -> TOutsourceShipment:
        shipment.deleted_at = now_naive()
        await self.session.flush()
        return shipment

    # ===== 单条 =====
    async def get_by_id(
        self, shipment_id: int, *, include_deleted: bool = False,
    ) -> TOutsourceShipment | None:
        result = await self.session.get(TOutsourceShipment, shipment_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def get_open_by_batch_id(
        self, batch_id: int,
    ) -> TOutsourceShipment | None:
        """按 batch_id 查唯一 OUTSOURCING 开口 shipment。"""
        stmt = (
            select(TOutsourceShipment)
            .where(TOutsourceShipment.deleted_at.is_(None))
            .where(TOutsourceShipment.batch_id == batch_id)
            .where(TOutsourceShipment.status == "OUTSOURCING")
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_open_by_part_company_process(
        self,
        *,
        part_id: int,
        company_id: int,
        process_id: int,
    ) -> TOutsourceShipment | None:
        """按 (part, company, process) 查最新一条 OUTSOURCING shipment（兜底）。"""
        stmt = (
            select(TOutsourceShipment)
            .where(TOutsourceShipment.deleted_at.is_(None))
            .where(TOutsourceShipment.part_id == part_id)
            .where(TOutsourceShipment.outsource_company_id == company_id)
            .where(TOutsourceShipment.process_id == process_id)
            .where(TOutsourceShipment.status == "OUTSOURCING")
            .order_by(TOutsourceShipment.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 对账页 =====
    def _build_reconciliation_stmt(
        self,
        *,
        company_id: int,
        part_ids_in: list[int] | None = None,
        sent_from=None,
        sent_to=None,
        received_from=None,
        received_to=None,
    ):
        stmt = select(TOutsourceShipment).where(
            TOutsourceShipment.deleted_at.is_(None),
            TOutsourceShipment.outsource_company_id == company_id,
            TOutsourceShipment.status.in_(["OUTSOURCING", "RECEIVED"]),
        )
        if part_ids_in is not None:
            stmt = stmt.where(TOutsourceShipment.part_id.in_(part_ids_in))
        if sent_from is not None:
            stmt = stmt.where(TOutsourceShipment.sent_at >= sent_from)
        if sent_to is not None:
            stmt = stmt.where(TOutsourceShipment.sent_at <= sent_to)
        if received_from is not None:
            stmt = stmt.where(TOutsourceShipment.received_at >= received_from)
        if received_to is not None:
            stmt = stmt.where(TOutsourceShipment.received_at <= received_to)
        return stmt

    async def list_reconciliation_for_company(
        self,
        *,
        company_id: int,
        part_ids_in: list[int] | None = None,
        sent_from=None,
        sent_to=None,
        received_from=None,
        received_to=None,
        sort_by=None,
        sort_dir=None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TOutsourceShipment]:
        if part_ids_in is not None and not part_ids_in:
            return []
        stmt = self._build_reconciliation_stmt(
            company_id=company_id,
            part_ids_in=part_ids_in,
            sent_from=sent_from, sent_to=sent_to,
            received_from=received_from, received_to=received_to,
        )
        if sort_by == OutsourceSentPartSortKey.PRICE:
            col = TOutsourceShipment.unit_price
        elif sort_by == OutsourceSentPartSortKey.RECEIVED_AT:
            col = TOutsourceShipment.received_at
        else:
            col = TOutsourceShipment.sent_at
        order = col.asc().nulls_last() if sort_dir == SortDir.ASC else col.desc().nulls_last()
        stmt = stmt.order_by(order, TOutsourceShipment.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_reconciliation_for_company(
        self,
        *,
        company_id: int,
        part_ids_in: list[int] | None = None,
        sent_from=None,
        sent_to=None,
        received_from=None,
        received_to=None,
    ) -> int:
        if part_ids_in is not None and not part_ids_in:
            return 0
        stmt = self._build_reconciliation_stmt(
            company_id=company_id,
            part_ids_in=part_ids_in,
            sent_from=sent_from, sent_to=sent_to,
            received_from=received_from, received_to=received_to,
        ).with_only_columns(func.count(TOutsourceShipment.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== in-flight =====
    async def list_in_flight(
        self,
        *,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[TOutsourceShipment, "TPart", "TPartBatch | None"]]:
        """返回外协中批次 + 左联开口 shipment。"""
        from model import TPart, TPartBatch

        stmt = (
            select(TPartBatch, TPart)
            .join(TPart, TPartBatch.part_id == TPart.id)
            .where(TPartBatch.deleted_at.is_(None))
            .where(TPartBatch.status == "OUTSOURCE")
            .where(TPart.deleted_at.is_(None))
        )
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    TPart.drawing_no.ilike(like),
                    TPart.name.ilike(like),
                    TPart.serial_no.ilike(like),
                )
            )
        stmt = stmt.order_by(TPartBatch.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        rows = list(result.all())  # list[tuple[TPartBatch, TPart]]

        # 左联开口 shipment
        batch_ids = [b.id for b, _ in rows]
        shipment_map: dict[int, TOutsourceShipment] = {}
        if batch_ids:
            ship_stmt = (
                select(TOutsourceShipment)
                .where(TOutsourceShipment.deleted_at.is_(None))
                .where(TOutsourceShipment.batch_id.in_(batch_ids))
                .where(TOutsourceShipment.status == "OUTSOURCING")
            )
            ship_res = await self.session.execute(ship_stmt)
            for s in ship_res.scalars().all():
                if s.batch_id is not None:
                    shipment_map[s.batch_id] = s

        out: list[tuple[TOutsourceShipment, TPart, TPartBatch | None]] = []
        for batch, part in rows:
            out.append((shipment_map.get(batch.id), part, batch))
        return out

    async def count_in_flight(
        self,
        *,
        keyword: str | None = None,
    ) -> int:
        from model import TPart, TPartBatch

        stmt = (
            select(func.count(TPartBatch.id))
            .join(TPart, TPartBatch.part_id == TPart.id)
            .where(TPartBatch.deleted_at.is_(None))
            .where(TPartBatch.status == "OUTSOURCE")
            .where(TPart.deleted_at.is_(None))
        )
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    TPart.drawing_no.ilike(like),
                    TPart.name.ilike(like),
                    TPart.serial_no.ilike(like),
                )
            )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
