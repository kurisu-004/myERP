"""菜单主表（t_menu）+ 角色↔菜单关联（t_role_menu）。

设计要点：
- 邻接表：`parent_id` 指向自身，NULL = 顶层。CheckConstraint 防单行自环
  （与 t_customer.parent_id 风格一致，DB 层无物理外键）。
- `code` 是稳定 API（id 是雪花，对人不友好）；唯一索引走 partial unique
  `WHERE deleted_at IS NULL`（用 `Index(unique=True, postgresql_where=...)`
  —— `UniqueConstraint` 不支持 `postgresql_where`，参考 t_user.username 的写法）。
- t_role_menu 是简单 (role, menu_id) 多对多。role 字符串复用 UserRole.value。
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


class TMenu(Base, AuditMixin):
    """菜单主表（邻接表）。

    - `code` 业务唯一键（uk_t_menu_code，partial unique 排除软删行）。
    - `path` 为 NULL 表示这是分组节点（前端渲染为 <el-sub-menu>）；
      非 NULL 才是叶子节点（<el-menu-item>），同时是点击跳转的 URL。
    - `icon` 是 Element-Plus 图标组件名（如 'House'），前端按名查表。
    """

    __tablename__ = "t_menu"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)
    # 逻辑外键 -> t_menu.id（无 DB 约束）。service 层校验防深循环。
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        Index(
            "uk_t_menu_code",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_t_menu_no_self_loop",
        ),
        Index("ix_t_menu_parent_id", "parent_id"),
        Index("ix_t_menu_deleted_at", "deleted_at"),
    )


class TRoleMenu(Base, AuditMixin):
    """角色↔菜单 N:M 关联。

    同一 (role, menu_id) 仅一条有效行；删除靠 soft_delete。
    """

    __tablename__ = "t_role_menu"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # 逻辑外键 -> t_menu.id（无 DB 约束）。
    menu_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index(
            "uk_t_role_menu_role_menu",
            "role",
            "menu_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_t_role_menu_role", "role"),
        Index("ix_t_role_menu_menu_id", "menu_id"),
        Index("ix_t_role_menu_deleted_at", "deleted_at"),
    )