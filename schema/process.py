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
    sort_order: int = Field(default=0, ge=0)
    description: str | None = Field(default=None, max_length=200)
    requires_approval: bool = Field(
        default=True,
        description=(
            "外协工序是否需要报价审批。"
            "OUTSOURCE 默认 True（走原有报价 + MANAGER 审批 + 发送流程）；"
            "INHOUSE 由 service 层强制改为 False（INHOUSE 不进入外协流程）。"
        ),
    )

    @field_validator("code", "name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class ProcessUpdateRequest(BaseModel):
    """更新工序字段。code 不可改（业务唯一键）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    category: ProcessCategory | None = None
    sort_order: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=200)
    requires_approval: bool | None = Field(
        default=None,
        description="None = 不改；True/False 直接覆盖",
    )

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class ProcessOut(BaseModel):
    """工序展示用出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    code: str
    name: str
    category: ProcessCategory
    sort_order: int
    description: str | None = None
    requires_approval: bool = Field(
        description="外协工序是否需要报价审批（INHOUSE 工序固定为 False）",
    )
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