"""菜单树响应 schema。

`MenuNodeOut` 是递归模型：每个节点可挂 `children`。ID 字段统一走
`IdStr` / `IdStrNonNull`，序列化到 JSON 时变成字符串以避免 JS
`Number.MAX_SAFE_INTEGER` 精度截断。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schema._types import IdStr, IdStrNonNull


class MenuNodeOut(BaseModel):
    """单个菜单节点 + 子节点。

    - `path` 为 NULL 表示分组节点（前端渲染为 <el-sub-menu>）。
    - `parent_id` 顶层节点为 NULL。
    - `icon` 是 Element-Plus 图标组件名（如 'House'）；前端按名查表渲染。
    """

    id: IdStrNonNull
    parent_id: IdStr = None
    code: str
    title: str
    path: str | None = None
    icon: str | None = None
    sort_order: int
    children: list["MenuNodeOut"] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


MenuNodeOut.model_rebuild()