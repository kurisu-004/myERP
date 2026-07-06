"""CNC 程序（G 代码）文件相关 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from schema._types import IdStrNonNull


class CncProgramOut(BaseModel):
    """CNC 程序文件展示用出参。

    `download_url` 每次请求即时签发，**不**存 DB；前端在过期前（默认 15 min）
    可直接浏览器 GET 下载。`id` / `part_id` 序列化为字符串，避免 JS 精度截断。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    part_id: IdStrNonNull
    file_type: str = Field(description="G 代码扩展名大写（NC / TAP / ...）")
    original_filename: str
    file_size: int
    content_type: str
    download_url: str = Field(description="COS 临时签名 URL，默认 15 分钟有效")
    upload_status: str
    created_at: datetime
