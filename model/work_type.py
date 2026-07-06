"""工种 (WorkType) — 工人所属的工种类别（如 车床 / 铣床 / 品检 / 送货司机）。

注意：
- 项目约定 **不在 DB 层加物理外键**。
  被 `t_worker.work_type_id` / `t_work_type_process.work_type_id` 逻辑引用，
  软删 / 重名校验由 service 层处理。
- 审计字段由 `AuditMixin` 提供。
- `code` 是工种代码（如 "车床"），业务层不可变；改名通过删除 + 重建实现。
"""
from sqlalchemy import BigInteger, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TWorkType(Base, AuditMixin):
    """工种主表。

    Columns:
        id (BigInteger PK): 雪花 ID
        code (String 32): 工种代码（业务唯一键），如 "车床" / "CNC操机"
        name (String 50): 工种显示名，如 "车床工"
        description (String 200, nullable): 备注
        sort_order (Integer): 列表排序
        (audit 5 fields via AuditMixin)
    """

    __tablename__ = "t_work_type"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )

    code: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="工种代码（业务唯一键，不可变）",
    )
    name: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="工种名称（前端显示）",
    )
    description: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
        comment="可选描述",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
        comment="显示顺序",
    )

    __table_args__ = (
        Index(
            "uk_t_work_type_code",
            "code", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_t_work_type_code", "code"),
        Index("ix_t_work_type_deleted_at", "deleted_at"),
    )