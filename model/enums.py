import enum

from sqlalchemy import Enum as SAEnum


class PartStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROCESS = "IN_PROCESS"
    DONE = "DONE"
    REJECTED = "REJECTED"


PART_STATUS_ENUM = SAEnum(
    PartStatus,
    name="part_status",
    values_callable=lambda e: [m.value for m in e],
)
