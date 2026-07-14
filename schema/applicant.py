"""申请人 (Applicant) Pydantic schema。

所有 customer_id 字段均为雪花 ID 字符串（19 位），与 CLAUDE.md §3
「雪花 ID 入参必须用 str 类型」一致。service 层 int() 转回。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema._types import IdStrNonNull


class ApplicantOut(BaseModel):
    """单条申请人展示用出参。

    `customer_name` 由 service 层补（一级客户的 name），便于前端直接显示。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    name: str
    customer_id: IdStrNonNull
    customer_name: str | None = None
    created_at: datetime
    updated_at: datetime


class ApplicantCreateRequest(BaseModel):
    """新增申请人。`customer_id` 必须指向一级客户（service 层校验）。"""

    name: str = Field(min_length=1, max_length=50)
    customer_id: str = Field(description="一级客户 id（雪花 ID 字符串，必须 parent_id IS NULL）")

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class ApplicantUpdateRequest(BaseModel):
    """更新申请人字段（全部可选，只更新传入的非 None 字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    customer_id: str | None = Field(default=None, description="一级客户 id（雪花 ID 字符串）")

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class ApplicantListQuery(BaseModel):
    """申请人列表查询参数。"""

    customer_id: str | None = Field(default=None, description="所属一级客户 id（雪花 ID 字符串）")
    name_like: str | None = Field(default=None, description="姓名模糊匹配")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ApplicantListOut(BaseModel):
    items: list[ApplicantOut]
    total: int
    limit: int
    offset: int


class ApplicantSearchQuery(BaseModel):
    """申请人前序查询参数（零件/装配体对话框自动补全用）。"""

    customer_id: str = Field(..., description="一级客户 id（雪花 ID 字符串）")
    name_prefix: str | None = Field(default=None, max_length=50)
    limit: int = Field(default=20, ge=1, le=100)


class BulkApplicantItem(BaseModel):
    """批量 get-or-create 的入参单条。

    `customer_id` 允许传该申请人实际挂载的 L2 客户 id（应标 Excel 解析后
    每行的 deptName → L2 客户）；service 层会沿 `parent_id` 上溯到 L1 根再
    走 `get_or_create`。
    """

    name: str = Field(min_length=1, max_length=50)
    customer_id: str = Field(description="客户 id（雪花 ID 字符串；L1/L2 均可，service 内部上溯到 L1 根）")

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class BulkApplicantOut(BaseModel):
    """批量 get-or-create 的出参单条。"""

    name: str
    customer_id: IdStrNonNull
    applicant_id: IdStrNonNull


class BulkApplicantRequest(BaseModel):
    """批量 get-or-create 的请求体。"""

    items: list[BulkApplicantItem] = Field(min_length=1, max_length=500)