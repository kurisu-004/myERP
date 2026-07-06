"""CNC 程序（G 代码）文件元数据 ORM。

`t_cnc_program` 存的是 COS 对象的引用 + 文件元数据；物理文件在 COS。
与图纸文件（`t_drawing_file`）分表：CNC 程序是编程员在“编程中”阶段产出的
数控程序文件（`.nc` / `.tap` / `.cnc` / `.mpf` / `.ngc` 等），只归属到零件。

注意：
- `part_id` 是逻辑外键，DB 层无 FK 约束（遵守项目「禁止物理外键」约定）；
  service 层负责校验目标行存在 + 未软删。
- `upload_status` 反映 DB 行是否已成功对应 COS 对象（PENDING / READY / FAILED）。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TCncProgram(Base, AuditMixin):
    """零件的 CNC 程序（G 代码）文件元数据。"""

    __tablename__ = "t_cnc_program"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # 逻辑外键 → t_part.id。
    part_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )

    file_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="G 代码扩展名大写（NC / TAP / CNC / MPF / NGC）",
    )
    object_key: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="COS 对象 key"
    )
    original_filename: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    upload_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="READY",
        server_default="READY",
        comment="PENDING / READY / FAILED",
    )

    __table_args__ = (
        Index("ix_t_cnc_program_part", "part_id"),
    )
