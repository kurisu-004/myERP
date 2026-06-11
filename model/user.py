from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.base import Base


class TUser(Base):
    __tablename__ = "t_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    orders: Mapped[list["TOrder"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )
