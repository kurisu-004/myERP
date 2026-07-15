"""外协公司 ↔ 工序 多对多映射（t_outsource_company_process）。

- 与 t_work_type_process（工种↔工序）结构完全对称，参见 model/work_type_process.py。
- 软删支持：service 端 `set_outsource_company_processes` 走 delete-then-insert 模式。
- 审计字段由 `AuditMixin` 提供（junction 表用量轻，但保持审计一致性）。
- 不在 DB 层加物理外键：逻辑引用 t_outsource_company / t_process。
- DB partial unique 索引 `(outsource_company_id, process_id) WHERE deleted_at IS NULL` 防重。
- CheckConstraint 防 outsource_company_id == process_id 的奇怪数据
  （snowflake 共享 BigInteger 空间，避免 ID 撞车手误）。
"""
from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TOutsourceCompanyProcess(Base, AuditMixin):
    """外协公司 ↔ 工序 映射表。

    Columns:
        id (BigInteger PK): 雪花 ID
        outsource_company_id (BigInteger): 逻辑外键 → t_outsource_company.id
        process_id (BigInteger): 逻辑外键 → t_process.id（通常 category=OUTSOURCE）
        sort_order (Integer): 工序在该公司能力清单内的显示顺序
        (audit 5 fields via AuditMixin)
    """

    __tablename__ = "t_outsource_company_process"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    outsource_company_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="逻辑外键 → t_outsource_company.id",
    )
    process_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="逻辑外键 → t_process.id",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
        comment="工序在该公司能力清单内的显示顺序",
    )

    __table_args__ = (
        # 同一公司下同一工序只能出现一次（按 deleted_at 过滤）
        Index(
            "uk_t_outsource_company_process",
            "outsource_company_id", "process_id", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_t_outsource_company_process_company",
            "outsource_company_id",
        ),
        Index(
            "ix_t_outsource_company_process_process",
            "process_id",
        ),
        Index(
            "ix_t_outsource_company_process_deleted_at",
            "deleted_at",
        ),
        # 防御：outsource_company_id 与 process_id 类型相同 (snowflake 共享 BigInteger 空间)；
        # 此约束确保不会写出 outsource_company_id == process_id 的奇怪数据。
        CheckConstraint(
            "outsource_company_id <> process_id",
            name="ck_t_outsource_company_process_no_self_loop",
        ),
    )