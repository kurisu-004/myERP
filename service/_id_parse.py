"""Snowflake ID 入参解析辅助。

请求体里的 snowflake ID 字段一律用 str 承载（CLAUDE.md §3 「雪花 ID 入参
必须用 str 类型」），service 层拿到 string 后再 int() 转回给 repository。

集中在这里便于复用、便于统一错误码。
"""
from __future__ import annotations

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError


def parse_snowflake_id(value: str | int | None, *, field_name: str = "id") -> int | None:
    """str → int 解析。None / 空字符串直接返回 None；非空但无法转 int 抛 BizError。

    用法：
    - `customer_id_int = parse_snowflake_id(data.customer_id, field_name="customer_id")`
    - `applicant_id_int = parse_snowflake_id(data.applicant_id, field_name="applicant_id")`
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError) as e:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"{field_name} 必须是数字字符串：{value!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        ) from e