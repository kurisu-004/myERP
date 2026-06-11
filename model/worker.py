from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.base import Base


class TWorker(Base):
    __tablename__ = "t_worker"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    parts: Mapped[list["TPart"]] = relationship(
        back_populates="worker",
        lazy="raise",
    )
