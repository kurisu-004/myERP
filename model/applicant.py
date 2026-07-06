"""申请人 (Applicant) ORM。

设计要点：
- 每个申请人通过 `customer_id` 逻辑外键关联到 t_customer 的一级客户（根节点）；
  service 层校验 `customer.parent_id IS NULL`，二级客户不允许挂申请人。
- 同一一级客户下姓名不可重复（DB 用 partial unique index 保证，软删后允许重建）。
- 审计字段由 `AuditMixin` 提供，不重复声明。
"""
from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TApplicant(Base, AuditMixin):
    """申请人主表。

    - `name` 50 字符内（与 schema 层约束一致）。
    - `customer_id` 逻辑外键 → t_customer.id（一级客户）。
    """

    __tablename__ = "t_applicant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, comment="申请人姓名",
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True,
        comment="逻辑外键 → t_customer.id（一级客户）",
    )