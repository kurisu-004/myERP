"""图纸文件元数据 ORM。

`t_drawing_file` 存的是 COS 对象的引用 + 文件元数据；物理文件在 COS。
- 多页 PDF：整本上传一次，装配件与各子件各占一行 `t_drawing_file`，
  通过 `assembly_id` 或 `part_id` 关联，`page_index` 标记属于 PDF 的
  第几页（装配件的总图 `page_index=NULL`，子件从 2 开始）。
- STEP / DWG / DXF 等单文件：每行对应一个对象，`page_index=NULL`。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TDrawingFile(Base, AuditMixin):
    """零件 / 装配件的图纸文件元数据。

    物理文件存 COS；本表只存引用 key + 元数据。

    字段约束（DB 层 CheckConstraint 防止脏数据）：
    - `part_id` 与 `assembly_id` **必须有一个非空**（文件归属到具体
      装配件或具体子零件）；
    - 两者**不能同时非空**（一个文件只挂一个归属）。

    注意：
    - `part_id` / `assembly_id` 都是逻辑外键，DB 层无 FK 约束；service
      层负责校验目标行存在 + 未软删。
    - `upload_status` 反映"DB 行是否已经成功对应 COS 对象"：
      - `PENDING`：DB 已写但 COS 上传未开始或失败，可由后台 GC 清理；
      - `READY`：COS 上传成功，文件可用；
      - `FAILED`：上传失败，等待人工 / 脚本处理。
    """

    __tablename__ = "t_drawing_file"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # 逻辑外键 → t_part.id；NULL 表示挂在装配件上。
    part_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # 逻辑外键 → t_assembly.id；NULL 表示挂在具体子零件上。
    assembly_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )

    file_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="PDF / STEP / DWG / DXF（service 层校验大小写）",
    )
    object_key: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="COS 对象 key"
    )
    original_filename: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    page_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="多页 PDF 中该行指向的页码（从 1 开始）；非 PDF 场景 NULL",
    )
    upload_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="READY",
        server_default="READY",
        comment="PENDING / READY / FAILED",
    )

    __table_args__ = (
        CheckConstraint(
            "(part_id IS NOT NULL) <> (assembly_id IS NOT NULL)",
            name="ck_t_drawing_file_owner_xor",
        ),
        Index("ix_t_drawing_file_part_type", "part_id", "file_type"),
        Index("ix_t_drawing_file_assembly_type", "assembly_id", "file_type"),
    )