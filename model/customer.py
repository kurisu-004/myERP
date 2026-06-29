from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.audit import AuditMixin
from model.base import Base


class TCustomer(Base, AuditMixin):
    """客户树（邻接表）。

    典型结构：
        法拉电子 (parent_id=NULL)
        ├── 母排厂
        │   └── 一组
        └── 二厂
        路达 (parent_id=NULL)
        └── 开发一部

    注意：
    - 项目约定 **不在 DB 层加物理外键**，parent_id 的合法性（包括
      防自引用）由 service 层校验。
    - 审计字段（created_at / created_by / updated_at / updated_by / deleted_at）
      由 `AuditMixin` 提供，本类不重复声明。
    - 因为 DB 层无外键，自引用的 `parent` / `children` 关系如果用 ORM 表达
      会引入 join 推断复杂性。本类只保留 `parts`（跨表 1:N），parent/children
      通过 repository 显式查询（如 `WHERE id = :parent_id` 或递归 CTE）。
    """

    __tablename__ = "t_customer"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # 逻辑外键 -> t_customer.id（无 DB 约束）。取值校验在 service 层完成。
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )

    parts: Mapped[list["TPart"]] = relationship(
        "TPart",
        primaryjoin="TCustomer.id == TPart.customer_id",
        foreign_keys="[TPart.customer_id]",
        back_populates="customer",
        lazy="raise",
    )