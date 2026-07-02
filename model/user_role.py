"""账号角色多对多（t_user_role）。

一个账号可有多个角色；不同角色允许不同 scope：

- MANAGER       scope 通常为 NULL（全局）
- SHELF_ACCOUNT scope_type='shelf', scope_id=<shelf.id>（绑定到具体货架）
- CLERK         占位，本轮不实现
- INSPECTOR     占位，本轮不实现

复合唯一约束 `(user_id, role, scope_type, scope_id)` 在 (NULL, NULL)
情况下 PG 不视为重复（SQL 标准）。`add_role` 在 service 层显式校验。
"""
from sqlalchemy import (
    BigInteger,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TUserRole(Base, AuditMixin):
    """账号角色关联。

    注意：scoped 角色（SHELF_ACCOUNT）每 (user, shelf) 一行；
    非 scoped 角色（MANAGER）每 user 一行（其它字段 NULL）。
    """

    __tablename__ = "t_user_role"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    scope_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role",
            "scope_type",
            "scope_id",
            name="uk_t_user_role_user_role_scope",
        ),
        Index("ix_t_user_role_scope", "scope_type", "scope_id"),
    )
