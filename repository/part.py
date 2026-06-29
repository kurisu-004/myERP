from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.serial import SERIAL_MAX, SERIAL_MIN
from model import TPart
from model.enums import PartSortKey, PartStatus, SortDir


def _validate_status(value: str | PartStatus | None) -> PartStatus | None:
    """把字符串/枚举统一成 `PartStatus`；非法值抛 `ValueError`。"""
    if value is None:
        return None
    if isinstance(value, PartStatus):
        return value
    return PartStatus(value)


class PartRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, part: TPart) -> TPart:
        self.session.add(part)
        await self.session.flush()
        return part

    async def create_many(self, parts: list[TPart]) -> list[TPart]:
        self.session.add_all(parts)
        await self.session.flush()
        return parts

    # ===== 单条查询 =====
    async def get_by_id(
        self, part_id: int, *, include_deleted: bool = False
    ) -> TPart | None:
        part = await self.session.get(TPart, part_id)
        if part is None:
            return None
        if not include_deleted and part.deleted_at is not None:
            return None
        return part

    async def get_by_drawing_no(
        self, drawing_no: str, *, include_deleted: bool = False
    ) -> TPart | None:
        """按图号精确匹配（车间扫码定位用）。返回最近一条未软删记录。"""
        stmt = select(TPart).where(TPart.drawing_no == drawing_no)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        stmt = stmt.order_by(TPart.id.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 列表查询（核心：模糊查询 + 多维过滤 + 排序） =====
    async def list_with_filters(
        self,
        *,
        customer_id: int | None = None,
        status: PartStatus | str | None = None,
        is_urgent: bool | None = None,
        drawing_no_like: str | None = None,
        name_like: str | None = None,
        sort_by: PartSortKey = PartSortKey.PLANNED_DELIVERY_DATE,
        sort_dir: SortDir = SortDir.ASC,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TPart]:
        # 把 status 字符串强转成枚举：防止 service 漏校验导致 SQLAlchemy 报枚举错。
        status_enum = _validate_status(status)

        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            status=status_enum,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            include_deleted=include_deleted,
        )
        sort_col = {
            PartSortKey.PLANNED_DELIVERY_DATE: TPart.planned_delivery_date,
            PartSortKey.REQUEST_DATE: TPart.request_date,
            PartSortKey.CREATED_AT: TPart.created_at,
        }[sort_by]
        if sort_dir == SortDir.ASC:
            stmt = stmt.order_by(sort_col.asc(), TPart.id.desc())
        else:
            stmt = stmt.order_by(sort_col.desc(), TPart.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        customer_id: int | None = None,
        status: PartStatus | str | None = None,
        is_urgent: bool | None = None,
        drawing_no_like: str | None = None,
        name_like: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        status_enum = _validate_status(status)
        stmt = self._build_filter_stmt(
            customer_id=customer_id,
            status=status_enum,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            include_deleted=include_deleted,
        ).with_only_columns(func.count(TPart.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 更新 / 删除（软删） =====
    async def update(self, part: TPart) -> TPart:
        await self.session.flush()
        return part

    async def soft_delete(self, part: TPart) -> TPart:
        part.deleted_at = datetime.utcnow()
        await self.session.flush()
        return part

    # ===== 序列号 =====
    async def find_next_serial_for_code(self, code: str) -> str:
        """找指定一级客户代码（一个字母）的下一个可用序列号。

        序列号在一级客户范围内全局共享：
          - F1000 ~ F9999 是整个「法拉电子」的工单号池
          - L1000 ~ L9999 是整个「路达」的工单号池
        同一个一级客户下的所有二级叶子客户共享同一段号。
        释放时也是：完成/取消时该号回到所属一级客户的池里。

        搜索 [SERIAL_MIN, SERIAL_MAX]（闭区间）找最小空缺。
        """
        stmt = select(TPart.serial_no).where(
            TPart.serial_no.is_not(None),
            TPart.serial_no.like(f"{code}%"),
            TPart.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        used: set[int] = set()
        for (sn,) in result:
            if not sn:
                continue
            try:
                used.add(int(sn[1:]))
            except ValueError:
                continue
        for n in range(SERIAL_MIN, SERIAL_MAX + 1):
            if n not in used:
                return f"{code}{n}"
        # 9000 个号全被占，理论不会发生（unique index 会先报错）
        return f"{code}{SERIAL_MIN}"

    async def get_by_serial(
        self, serial_no: str, *, include_deleted: bool = False
    ) -> TPart | None:
        stmt = select(TPart).where(TPart.serial_no == serial_no)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 内部 =====
    def _build_filter_stmt(
        self,
        *,
        customer_id: int | None,
        status: PartStatus | None,
        is_urgent: bool | None,
        drawing_no_like: str | None,
        name_like: str | None,
        include_deleted: bool,
    ):
        stmt = select(TPart)
        if not include_deleted:
            stmt = stmt.where(TPart.deleted_at.is_(None))
        if customer_id is not None:
            stmt = stmt.where(TPart.customer_id == customer_id)
        if status is not None:
            stmt = stmt.where(TPart.status == status.value)
        if is_urgent is not None:
            stmt = stmt.where(TPart.is_urgent.is_(is_urgent))
        if drawing_no_like:
            # 大小写不敏感模糊匹配
            stmt = stmt.where(
                TPart.drawing_no.ilike(f"%{drawing_no_like}%")
            )
        if name_like:
            stmt = stmt.where(TPart.name.ilike(f"%{name_like}%"))
        return stmt