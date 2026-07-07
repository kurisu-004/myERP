"""客户相关的 Pydantic schema。

客户树是邻接表（parent_id 指向 t_customer.id），通过 `parent_name` 字段
把上级客户的名字一起返回，前端拿 `parent_name / name` 就能唯一定位节点。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema._types import IdStr, IdStrNonNull


class CustomerOut(BaseModel):
    """单条客户展示用出参。

    `id` / `parent_id` 序列化为字符串（雪花 ID 19 位，超 JS
    `Number.MAX_SAFE_INTEGER`），避免 JS 精度截断。DB 仍存 BigInteger，
    但已统一为雪花 ID（与其他业务表一致）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    name: str
    parent_id: IdStr = None
    parent_name: str | None = None


class CustomerCreateRequest(BaseModel):
    """新增客户。

    - `parent_id` 留空 → 一级客户（根）。
    - `parent_id` 非空 → 必须指向一个一级客户（service 层校验）。

    `parent_id` 是雪花 ID 字符串（与 CLAUDE.md §3 「雪花 ID 入参必须用
    str 类型」一致），service 层 int() 转回。
    """

    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = Field(
        default=None, description="父客户 id（雪花 ID 字符串）；NULL = 一级客户",
    )

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class CustomerUpdateRequest(BaseModel):
    """更新客户字段（全部可选，只更新传入的非 None 字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = Field(
        default=None,
        description="父客户 id（雪花 ID 字符串）；NULL = 一级客户；显式传 None 表示去父",
    )

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None