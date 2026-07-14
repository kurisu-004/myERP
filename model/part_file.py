"""零件 / 装配体的统一文件元数据 ORM。

2026-07-10 起合并了原来的 `t_drawing_file` (零件 / 装配体图纸) 与
`t_cnc_program` (G 代码)，新增 3D 模型与 CNC 设定单两类。所有文件都落
`t_part_file`，用 `kind` 字段 (`PartFileKind` 枚举) 区分：

- DRAWING           零件 / 子件的图纸 (PDF / PNG / JPG / TIFF / WEBP / HEIC...)，单文件约束
- THREE_D_MODEL     零件 3D 模型 (STEP / STP / IGES / IGS / STL / OBJ / 3MF)，单文件约束
- G_CODE            零件 CNC G 代码 (NC / TAP / CNC / MPF / NGC)，允许多版本
- SETUP_SHEET       零件 CNC 设定单 (PDF)，单文件约束
- ASSEMBLY_MASTER   装配体的总装图 (PDF)，单文件约束；
                    **polymorphic**: `part_id` 字段存装配体的 id（不是某个子件）
- CAD_2D            零件 CAD 源文件 (DWG / DXF)，单文件约束（2026-07-14 新增）

`part_id` 是 polymorphic：真实 `t_part.id` 或 `t_assembly.id` (后者仅当
kind = ASSEMBLY_MASTER)。DB 层无 FK 约束（遵守项目「禁止物理外键」约定）；
service 层校验目标行存在 + 未软删。

单文件约束 (除 G_CODE 外) 由部分唯一索引 `uk_t_part_file_single` 在
DB 层强制：`UNIQUE (part_id, kind) WHERE deleted_at IS NULL AND kind IN
('DRAWING','3D_MODEL','SETUP_SHEET','ASSEMBLY_MASTER','CAD_2D')`。

内容去重：列 `content_sha256 CHAR(64)` 存 SHA-256 hex（NULL 表示未计算 /
历史记录）。部分唯一索引 `uk_t_part_file_part_kind_sha` 在
`(part_id, kind, content_sha256)` 上强制同 part+kind+内容至多 1 行活跃；
跨 part 不共享。详见 `core.file_hash` 与 `service.part_file.upload`。

`upload_status` 反映「DB 行是否已经成功对应 COS 对象」：
- PENDING : DB 已写但 COS 上传未开始或失败，可由后台 GC 清理；
- READY   : COS 上传成功，文件可用；
- FAILED  : 上传失败，等待人工 / 脚本处理。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, CHAR, CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TPartFile(Base, AuditMixin):
    """零件 / 装配体的统一文件元数据。物理文件存 COS。"""

    __tablename__ = "t_part_file"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # polymorphic owner: 真实 t_part.id 或 t_assembly.id (后者仅当 kind=ASSEMBLY_MASTER)
    part_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET / ASSEMBLY_MASTER / CAD_2D",
    )

    file_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="扩展名大写（PDF / STEP / NC / ...），与 kind 配套",
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
    # SHA-256 hex (64 hex chars)；NULL = 历史记录未计算。详见模块 docstring。
    content_sha256: Mapped[str | None] = mapped_column(
        CHAR(64),
        nullable=True,
        comment="SHA-256 hex of file bytes（去重用）；NULL = 未计算 / 历史记录",
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('DRAWING','3D_MODEL','G_CODE','SETUP_SHEET',"
            "'ASSEMBLY_MASTER','CAD_2D')",
            name="ck_t_part_file_kind",
        ),
        Index("ix_t_part_file_part_kind", "part_id", "kind"),
        Index("ix_t_part_file_created_at", "created_at"),
    )