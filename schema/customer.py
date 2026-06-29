"""客户相关的 Pydantic schema。

客户树是邻接表（parent_id 指向 t_customer.id），通过 `parent_name` 字段
把上级客户的名字一起返回，前端拿 `parent_name / name` 就能唯一定位节点。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CustomerOut(BaseModel):
    """单条客户展示用出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None = None
    parent_name: str | None = None