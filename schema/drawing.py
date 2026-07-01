"""图纸文件相关 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from schema._types import IdStrNonNull


class DrawingFileOut(BaseModel):
    """图纸文件展示用出参。

    `owner_type` 由 service 拼好（"assembly" | "part"），
    `owner_id` 是对应表的主键。
    `download_url` 每次请求即时签发，**不**存 DB；前端在过期前（默认 15 min）
    可直接浏览器 GET 下载/预览。

    `id` / `owner_id` 序列化为字符串，避免 JS 精度截断。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    owner_type: str = Field(description="assembly / part")
    owner_id: IdStrNonNull
    file_type: str = Field(description="PDF / STEP / DWG / DXF")
    original_filename: str
    file_size: int
    content_type: str
    page_index: int | None = None
    download_url: str = Field(description="COS 临时签名 URL，默认 15 分钟有效")
    upload_status: str
    created_at: datetime


class DrawingFileListOut(BaseModel):
    items: list[DrawingFileOut]
    total: int
