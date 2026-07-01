from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema._types import IdStrNonNull

# 18 位身份证（最后一位可为 X）
ID_CARD_RE = re.compile(r"^\d{17}[\dXx]$")
# 中国大陆手机号（11 位，1 开头）
PHONE_CN_RE = re.compile(r"^\+?\d{6,20}$")


class WorkerCreateRequest(BaseModel):
    """新增工人。"""

    badge_code: str = Field(
        min_length=1, max_length=50, description="工牌扫码值（车间扫码端以此定位工人）"
    )
    name: str = Field(min_length=1, max_length=50)
    id_card_no: str | None = Field(
        default=None, max_length=18, description="身份证号（18 位）"
    )
    phone: str | None = Field(
        default=None, max_length=20, description="手机号（可带国际区号）"
    )

    @field_validator("badge_code", "name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("id_card_no")
    @classmethod
    def validate_id_card(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not ID_CARD_RE.match(v):
            raise ValueError("id_card_no must be 18 chars: 17 digits + digit/X")
        return v.upper()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().replace(" ", "").replace("-", "")
        if not PHONE_CN_RE.match(v):
            raise ValueError("phone must be 6-20 digits, optional leading +")
        return v


class WorkerUpdateRequest(BaseModel):
    """更新工人字段；只传需要改的。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    badge_code: str | None = Field(default=None, min_length=1, max_length=50)
    id_card_no: str | None = Field(default=None, max_length=18)
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("name", "badge_code")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None

    @field_validator("id_card_no")
    @classmethod
    def validate_id_card(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not ID_CARD_RE.match(v):
            raise ValueError("id_card_no must be 18 chars: 17 digits + digit/X")
        return v.upper()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().replace(" ", "").replace("-", "")
        if not PHONE_CN_RE.match(v):
            raise ValueError("phone must be 6-20 digits, optional leading +")
        return v


class WorkerOut(BaseModel):
    """工人展示用出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    badge_code: str
    name: str
    id_card_no: str | None = None
    phone: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WorkerListQuery(BaseModel):
    """工人列表查询参数。"""

    name_like: str | None = Field(default=None, description="姓名模糊匹配（ILIKE）")
    is_active: bool | None = Field(default=None, description="是否在职")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class WorkerListOut(BaseModel):
    items: list[WorkerOut]
    total: int
    limit: int
    offset: int