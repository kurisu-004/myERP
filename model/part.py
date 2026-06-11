from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.base import Base
from model.enums import PART_STATUS_ENUM, PartStatus


class TPart(Base):
    __tablename__ = "t_part"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[PartStatus] = mapped_column(
        PART_STATUS_ENUM,
        nullable=False,
        default=PartStatus.PENDING,
        server_default=PartStatus.PENDING.value,
        index=True,
    )

    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("t_order.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worker_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("t_worker.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    order: Mapped["TOrder"] = relationship(back_populates="parts", lazy="raise")
    worker: Mapped["TWorker | None"] = relationship(back_populates="parts", lazy="raise")
