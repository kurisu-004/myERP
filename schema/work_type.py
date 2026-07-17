"""工种 (WorkType) Pydantic schema。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema._types import IdStrNonNull


class WorkTypeCreateRequest(BaseModel):
    """新增工种。"""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    sort_order: int = Field(default=0, ge=0)

    @field_validator("code", "name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class WorkTypeUpdateRequest(BaseModel):
    """更新工种字段。code 不可改（业务唯一键）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class WorkTypeOut(BaseModel):
    """工种展示用出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    code: str
    name: str
    description: str | None = None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class WorkTypeListQuery(BaseModel):
    """工种列表查询参数。"""

    code_like: str | None = Field(default=None, description="工种代码模糊匹配")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class WorkTypeListOut(BaseModel):
    items: list[WorkTypeOut]
    total: int
    limit: int
    offset: int