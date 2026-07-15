"""货架相关 Pydantic schema。

货架本身不带账号；账号关联走 t_user_role(scope=shelf.id)。

2026-07-10 共享 HMI 多货架改造（plan: glowing-giggling-liskov.md）：
- `ShelfOut` 加 `display_order`
- 新增 `ShelfForReturnOut` —— RETURN 流程卡片网格 picker 用
  （含 current_load / mapped_process_codes / is_recommended）
- 新增 `ShelfForReturnListOut` —— picker 响应（含 recommended_shelf_id）
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import ShelfZone
from schema._types import IdStrNonNull


class ShelfOut(BaseModel):
    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    code: str
    name: str
    zone: str                          # ShelfZone.value
    location: str | None = None
    is_active: bool
    account_count: int = 0             # 该 shelf 上挂的 SHELF_ACCOUNT role 数（未软删）
    display_order: int = 0             # 物理顺序（0=未设置；manager 在 ShelfList 后台手填）
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShelfCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    zone: ShelfZone
    location: str | None = Field(default=None, max_length=200)
    display_order: int = Field(
        default=0, ge=0,
        description="物理顺序；0=未设置。manager 在 ShelfList 后台调整。",
    )

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
    display_order: int | None = Field(default=None, ge=0)


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


# ============================================================
# 共享 HMI RETURN 卡片网格 picker 用 schema
# ============================================================
class ShelfForReturnOut(BaseModel):
    """单架卡片数据：含在架件数 + 映射工序 + 推荐标记。

    `is_recommended=True` 的卡片在 UI 上加 ✓ 推荐 徽章 + 橙色边框；
    picker 弹窗时默认选中推荐架（worker 一键「完成」即可）。
    """

    id: IdStrNonNull
    code: str
    name: str
    location: str | None = None
    display_order: int
    current_load: int = 0             # 当前在架件数（status=IN_PROCESS + holder=shelf）
    mapped_process_codes: list[str] = Field(
        default_factory=list,
        description="已映射工序的 code 列表（前端 chips 展示）",
    )
    is_recommended: bool = False


class ShelfForReturnListOut(BaseModel):
    """`GET /shelves/for-return?next_process_id=...` 响应。

    `recommended_shelf_id` 必传（picker 静默默认）；`items` 按
    (current_load ASC, display_order ASC, code ASC) 排序。
    """

    items: list[ShelfForReturnOut]
    recommended_shelf_id: str         # IdStrNonNull 等价
