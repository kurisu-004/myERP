from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.base import Base

if TYPE_CHECKING:
    from model.user import TUser


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TRole(Base):
    __tablename__ = "t_role"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_builtin: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    permissions: Mapped[list["TPermission"]] = relationship(
        secondary="t_role_permission",
        back_populates="roles",
        viewonly=True,
        lazy="raise",
    )
    role_permissions: Mapped[list["TRolePermission"]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    user_roles: Mapped[list["TUserRole"]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    users: Mapped[list["TUser"]] = relationship(
        secondary="t_user_role",
        back_populates="roles",
        viewonly=True,
        lazy="raise",
    )


class TPermission(Base):
    __tablename__ = "t_permission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("t_permission.id", ondelete="SET NULL"), nullable=True
    )
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    component: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    children: Mapped[list["TPermission"]] = relationship(
        "TPermission",
        back_populates="parent",
        remote_side="TPermission.parent_id",
        lazy="raise",
    )
    parent: Mapped["TPermission | None"] = relationship(
        "TPermission",
        back_populates="children",
        remote_side="TPermission.id",
        lazy="raise",
    )
    roles: Mapped[list["TRole"]] = relationship(
        secondary="t_role_permission",
        back_populates="permissions",
        viewonly=True,
        lazy="raise",
    )
    role_permissions: Mapped[list["TRolePermission"]] = relationship(
        back_populates="permission",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class TRolePermission(Base):
    __tablename__ = "t_role_permission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_role.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("t_permission.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    role: Mapped["TRole"] = relationship(back_populates="role_permissions", lazy="raise")
    permission: Mapped["TPermission"] = relationship(
        back_populates="role_permissions", lazy="raise"
    )


class TUserRole(Base):
    __tablename__ = "t_user_role"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_user.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("t_role.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user: Mapped["TUser"] = relationship(back_populates="user_roles", lazy="raise")
    role: Mapped["TRole"] = relationship(back_populates="user_roles", lazy="raise")
