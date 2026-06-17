"""时间工具，统一项目内 UTC 时间获取。"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回带 tzinfo 的当前 UTC 时间（aware）。

    项目约定：数据库时间列统一存 aware UTC，序列化时显式带 +00:00 后缀。
    """
    return datetime.now(timezone.utc)