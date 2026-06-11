from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.base import Base


class TOrder(Base):
    __tablename__ = "t_order"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE"),
        index=True,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    user: Mapped["TUser"] = relationship(back_populates="orders", lazy="raise")

    parts: Mapped[list["TPart"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="raise",
    )
