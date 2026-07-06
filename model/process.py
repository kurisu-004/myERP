"""工序 (Process) — 零件的加工步骤（如 车 / 铣 / 磨 / 热处理 / 电镀）。

- INHOUSE = 自产（在车间内加工）；OUTSOURCE = 外协（外发给供应商）。
- `is_inspection` 标识该工序是否品检工序（UI 提示用，不影响取件过滤逻辑）。
- 被 `t_part.next_process_id` / `t_work_type_process.process_id` 逻辑引用，
  软删 / 重名校验由 service 层处理。
- 审计字段由 `AuditMixin` 提供。
- `code` 是业务唯一键，不可变。
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TProcess(Base, AuditMixin):
    """工序主表。

    Columns:
        id (BigInteger PK): 雪花 ID
        code (String 32): 工序代码（业务唯一键），如 "车" / "CNC"
        name (String 50): 工序显示名，如 "车床加工"
        category (String 16): 自产 / 外协；DB CheckConstraint 强校验
        is_inspection (Boolean): 是否品检工序；UI 提示
        sort_order (Integer): 列表排序
        description (String 200, nullable): 备注
        (audit 5 fields via AuditMixin)
    """

    __tablename__ = "t_process"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )

    code: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="工序代码（业务唯一键，不可变）",
    )
    name: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="工序名称（前端显示）",
    )
    category: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="INHOUSE 自产 / OUTSOURCE 外协",
    )
    is_inspection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="是否品检工序（UI 提示；不影响取件过滤）",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
        comment="显示顺序",
    )
    description: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
        comment="可选描述",
    )

    __table_args__ = (
        Index(
            "uk_t_process_code",
            "code", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_t_process_code", "code"),
        Index("ix_t_process_category", "category"),
        Index("ix_t_process_deleted_at", "deleted_at"),
        CheckConstraint(
            "category IN ('INHOUSE', 'OUTSOURCE')",
            name="ck_t_process_category",
        ),
    )