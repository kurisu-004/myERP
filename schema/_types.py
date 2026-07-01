"""Pydantic 通用类型定义。

`IdStr`：把 int 主键 / 外键在 JSON 响应中序列化为字符串，避免 JS 前端
`Number.MAX_SAFE_INTEGER` 精度截断。约定：

- 仅影响序列化（出参 / response_model），不影响反序列化（入参仍可接收 int）。
- DB 列保持 `BigInteger` 不动，**不**在持久化层做字符串转换。
- 路由 path 参数仍按字符串拼接，但 service 层 / repository 层接收时用 int。

参考业界共识：
- JSON:API 规范：id MUST be string
- OpenAPI 社区建议：跨端 ID 优先 string
- Stripe / Discord / GitHub GraphQL：统一字符串 ID
"""
from __future__ import annotations

from typing import Annotated, Optional

from pydantic import PlainSerializer


def _id_to_str(v: int | None) -> str | None:
    """int 主键 → str。可空入参兼容 nullable 外键列。"""
    if v is None:
        return None
    return str(v)


# 可空主键 / 外键。Optional[int] 让 Pydantic 接受 None；
# serializer 负责把 int 转 str，None 直传 None。
IdStr = Annotated[
    Optional[int],
    PlainSerializer(
        _id_to_str,
        return_type=Optional[str],
        when_used="json",
    ),
]
"""序列化到 JSON 时把 int 转 str（None 直传 None）；Python 内部仍是 int 或 None。

使用方式（字段必须有显式默认值，否则 Pydantic 视作必填）：
    assembly_id: IdStr = None
    parent_id: IdStr = None
"""


IdStrNonNull = Annotated[
    int,
    PlainSerializer(
        lambda v: str(v),
        return_type=str,
        when_used="json",
    ),
]
"""非空 int 主键 / 外键的字符串化版本。"""