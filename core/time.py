"""统一时间工具。

本项目 DB 列一律 `timestamp without time zone`（naive），容器 TZ=Asia/Shanghai。
所有 Python 端写入（`deleted_at` / `last_login_at` / `placed_at` / WS `ts` 等）
必须用 `now_naive()` 拿到「看起来就是 Shanghai 当前时间」的 naive datetime，
避免 `datetime.utcnow()` 写出来是 UTC（与 DB `now()` 差 8h）。

`SHANGHAI_TZ` 显式构造 `timezone(timedelta(hours=8))`，**不**依赖环境 `time.tzname`，
CI runner TZ=UTC 时也输出 Shanghai 当前时间。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 项目统一 TZ：与容器 TZ 环境变量保持一致（Asia/Shanghai，UTC+8）
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_naive() -> datetime:
    """返回 Asia/Shanghai 当前时间的 naive datetime（无 tzinfo）。

    1. 先取 `datetime.now(SHANGHAI_TZ)` 拿到带 TZ 的 aware dt；
    2. `.replace(tzinfo=None)` 去掉 tzinfo → naive；
    3. 与 DB `timestamp without time zone` 列严格兼容（asyncpg codec 不会自动加 TZ）。
    """
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def now_shanghai_iso() -> str:
    """WS 推送 / 前端展示用：ISO 字符串 + 'Z' 后缀。

    数字本身是 Shanghai 当前时间，但带 'Z' 后缀让前端 `new Date(iso)` 视为 UTC，
    再按浏览器本地 TZ（Shanghai）显示 → 仍然是正确的 Shanghai 时间。

    注意：本质是 Shanghai 当前时间 + 「Z 后缀」语法约定，不修改值。
    """
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%dT%H:%M:%SZ")
