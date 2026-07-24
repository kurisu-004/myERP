"""零件 / 装配体的统一文件 schema（2026-07-10 起取代 `DrawingFileOut` / `CncProgramOut`）。

`owner_id` 是 polymorphic：
- DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET：真实 t_part.id
- ASSEMBLY_MASTER：t_assembly.id

`id` / `owner_id` 序列化为字符串，避免 JS 精度截断。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from schema._types import IdStrNonNull


class PartFileOut(BaseModel):
    """零件 / 装配体的统一文件展示用出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    owner_id: IdStrNonNull
    kind: str = Field(
        description="DRAWING / 3D_MODEL / G_CODE / SETUP_SHEET / ASSEMBLY_MASTER / CAD_2D",
    )
    file_type: str = Field(description="PDF / STEP / NC / ...")
    original_filename: str
    file_size: int
    content_type: str
    upload_status: str
    content_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 hex (64 chars)；NULL = 历史记录未计算。"
            "前端可基于此判断是否已存在去重命中。"
        ),
    )
    created_at: datetime
    paired_file_id: str | None = Field(
        default=None,
        description="关联的配对文件ID（G_CODE <-> SETUP_SHEET）；NULL=未配对",
    )
    download_url: str = Field(description="COS 临时签名 URL，默认 15 分钟有效")