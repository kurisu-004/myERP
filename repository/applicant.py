"""申请人 (Applicant) 数据访问。"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model import TApplicant


class ApplicantRepository:
    """t_applicant 数据访问。

    提供：
    - 标准 CRUD（create / get_by_id / list_by_ids / update / soft_delete）
    - 多维过滤列表 + 计数（list_with_filters / count_with_filters）
    - 幂等查询：find_by_name_and_customer（get_or_create 用）
    - 前序查询：search_by_name_prefix（零件对话框申请人下拉用）
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ===== 写入 =====
    async def create(self, applicant: TApplicant) -> TApplicant:
        self.session.add(applicant)
        await self.session.flush()
        return applicant

    # ===== 单条 =====
    async def get_by_id(
        self, applicant_id: int, *, include_deleted: bool = False
    ) -> TApplicant | None:
        result = await self.session.get(TApplicant, applicant_id)
        if result is None:
            return None
        if not include_deleted and result.deleted_at is not None:
            return None
        return result

    async def list_by_ids(
        self, applicant_ids: list[int], *, include_deleted: bool = False
    ) -> list[TApplicant]:
        if not applicant_ids:
            return []
        stmt = select(TApplicant).where(TApplicant.id.in_(applicant_ids))
        if not include_deleted:
            stmt = stmt.where(TApplicant.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 多维过滤列表 + 计数 =====
    async def list_with_filters(
        self,
        *,
        customer_id: int | None = None,
        name_like: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TApplicant]:
        stmt = select(TApplicant)
        if not include_deleted:
            stmt = stmt.where(TApplicant.deleted_at.is_(None))
        if customer_id is not None:
            stmt = stmt.where(TApplicant.customer_id == customer_id)
        if name_like:
            stmt = stmt.where(TApplicant.name.ilike(f"%{name_like}%"))
        stmt = stmt.order_by(
            TApplicant.customer_id.asc(), TApplicant.name.asc(), TApplicant.id.asc(),
        ).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_with_filters(
        self,
        *,
        customer_id: int | None = None,
        name_like: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(TApplicant)
        if not include_deleted:
            stmt = stmt.where(TApplicant.deleted_at.is_(None))
        if customer_id is not None:
            stmt = stmt.where(TApplicant.customer_id == customer_id)
        if name_like:
            stmt = stmt.where(TApplicant.name.ilike(f"%{name_like}%"))
        stmt = stmt.with_only_columns(func.count(TApplicant.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ===== 幂等查询（get_or_create） =====
    async def find_by_name_and_customer(
        self,
        name: str,
        customer_id: int,
        *,
        include_deleted: bool = False,
    ) -> TApplicant | None:
        """精确匹配 (name, customer_id)；用于服务层 get_or_create。"""
        stmt = select(TApplicant).where(
            TApplicant.name == name,
            TApplicant.customer_id == customer_id,
        )
        if not include_deleted:
            stmt = stmt.where(TApplicant.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ===== 前序查询（零件对话框申请人下拉） =====
    async def search_by_name_prefix(
        self,
        name_prefix: str,
        customer_id: int,
        *,
        limit: int = 20,
    ) -> list[TApplicant]:
        """`name LIKE prefix || '%'` 前序匹配；按 customer 范围筛选。

        与 list_with_filters 的 name_like（%xx%）区别：这里走前缀索引更快，
        也是用户要求的「前序查询」语义。
        """
        stmt = (
            select(TApplicant)
            .where(
                TApplicant.customer_id == customer_id,
                TApplicant.deleted_at.is_(None),
            )
        )
        prefix = name_prefix.strip()
        if prefix:
            # 转义 % / _，其余原样拼接
            escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(TApplicant.name.like(f"{escaped}%", escape="\\"))
        stmt = stmt.order_by(TApplicant.name.asc(), TApplicant.id.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ===== 更新 / 软删 =====
    async def update(self, applicant: TApplicant) -> TApplicant:
        await self.session.flush()
        return applicant

    async def soft_delete(self, applicant: TApplicant) -> TApplicant:
        applicant.deleted_at = datetime.utcnow()
        await self.session.flush()
        return applicant