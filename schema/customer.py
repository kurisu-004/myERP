"""客户相关的 Pydantic schema。

客户树是邻接表（parent_id 指向 t_customer.id），通过 `parent_name` 字段
把上级客户的名字一起返回，前端拿 `parent_name / name` 就能唯一定位节点。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from schema._types import IdStr, IdStrNonNull


class CustomerOut(BaseModel):
    """单条客户展示用出参。

    `id` / `parent_id` 序列化为字符串，避免 JS `Number.MAX_SAFE_INTEGER`
    精度截断。DB 仍存 BigInteger / auto-increment int。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    name: str
    parent_id: IdStr = None
    parent_name: str | None = None