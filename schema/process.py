"""工序 (Process) Pydantic schema。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import ProcessCategory
from schema._types import IdStrNonNull


class ProcessCreateRequest(BaseModel):
    """新增工序。"""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=50)
    category: ProcessCategory = Field(
        description="INHOUSE 自产 / OUTSOURCE 外协",
    )
    is_inspection: bool = Field(default=False, description="是否品检工序")
    sort_order: int = Field(default=0, ge=0)
    description: str | None = Field(default=None, max_length=200)

    @field_validator("code", "name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class ProcessUpdateRequest(BaseModel):
    """更新工序字段。code 不可改（业务唯一键）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    category: ProcessCategory | None = None
    is_inspection: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class ProcessOut(BaseModel):
    """工序展示用出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    code: str
    name: str
    category: ProcessCategory
    is_inspection: bool
    sort_order: int
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class ProcessListQuery(BaseModel):
    """工序列表查询参数。"""

    code_like: str | None = Field(default=None, description="工序代码模糊匹配")
    category: ProcessCategory | None = Field(
        default=None, description="按类别过滤",
    )
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ProcessListOut(BaseModel):
    items: list[ProcessOut]
    total: int
    limit: int
    offset: int