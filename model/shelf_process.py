"""货架 ↔ 工序 多对多映射 (ShelfProcess).

- Manager 通过 UI 配置：决定每个货架能执行哪些工序。
- 软删支持：service 端 `set_for_shelf` 走 delete-then-insert 模式。
- 审计字段由 `AuditMixin` 提供（虽然 junction 表用量轻，但保持审计一致性）。
- 不在 DB 层加物理外键：逻辑引用 t_shelf / t_process。
"""
from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TShelfProcess(Base, AuditMixin):
    """货架 ↔ 工序 映射表。

    Columns:
        id (BigInteger PK): 雪花 ID
        shelf_id (BigInteger): 逻辑外键 → t_shelf.id
        process_id (BigInteger): 逻辑外键 → t_process.id
        sort_order (Integer): 工序在货架映射内的显示顺序
        (audit 5 fields via AuditMixin)
    """

    __tablename__ = "t_shelf_process"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )

    shelf_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="逻辑外键 → t_shelf.id",
    )
    process_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="逻辑外键 → t_process.id",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
        comment="工序在货架映射内的显示顺序",
    )

    __table_args__ = (
        # 同一货架下同一工序只能出现一次（按 deleted_at 过滤）
        Index(
            "uk_t_shelf_process",
            "shelf_id", "process_id", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_t_shelf_process_shelf", "shelf_id"),
        Index("ix_t_shelf_process_process", "process_id"),
        Index("ix_t_shelf_process_deleted_at", "deleted_at"),
        # 防御：shelf_id 与 process_id 类型不同 (snowflake 共享 BigInteger 空间)；
        # 此约束确保不会写出 shelf_id == process_id 的奇怪数据。
        CheckConstraint(
            "shelf_id <> process_id",
            name="ck_t_shelf_process_no_self_loop",
        ),
    )
