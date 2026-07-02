"""货架相关 Pydantic schema。

货架本身不带账号；账号关联走 t_user_role(scope=shelf.id)。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import ShelfZone
from schema._types import IdStrNonNull


class ShelfOut(BaseModel):
    id: IdStrNonNull
    code: str
    name: str
    zone: str                          # ShelfZone.value
    location: str | None = None
    is_active: bool
    account_count: int = 0             # 该 shelf 上挂的 SHELF_ACCOUNT role 数（未软删）
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShelfCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    zone: ShelfZone
    location: str | None = Field(default=None, max_length=200)

    @field_validator("zone", mode="before")
    @classmethod
    def _norm_zone(cls, v):
        if isinstance(v, ShelfZone):
            return v
        return ShelfZone(v)


class ShelfUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class ShelfListQuery(BaseModel):
    zone: ShelfZone | None = None
    is_active: bool | None = None
    limit: int = Field(default=200, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ShelfListOut(BaseModel):
    items: list[ShelfOut]
    total: int
    limit: int
    offset: int
