"""core.time helper 单元测试。

验证 `now_naive()` 返回 naive datetime（无 tzinfo）且时间值与
「UTC + 8h」一致；CI runner TZ=UTC 时也必须给 Shanghai 时间。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.time import SHANGHAI_TZ, now_naive, now_shanghai_iso


def test_now_naive_returns_naive_datetime() -> None:
    """返回值必须无 tzinfo（与 DB `timestamp without time zone` 列兼容）。"""
    n = now_naive()
    assert isinstance(n, datetime)
    assert n.tzinfo is None


def test_now_naive_matches_utc_plus_eight_hours() -> None:
    """now_naive() 应该与「UTC + 8h」在同一秒内（精度内）。"""
    # 2026-07-15 修复 deprecation：datetime.utcnow() 已废弃，
    # 改用 `datetime.now(timezone.utc)` 拿 timezone-aware 当前 UTC 时间，
    # 再 `.replace(tzinfo=None)` 转 naive 与 now_naive() 同源做减法。
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    expected = utc_now + timedelta(hours=8)
    actual = now_naive()
    # 允许 ±1s 误差（程序执行时间）
    delta = abs((actual - expected).total_seconds())
    assert delta < 1.0, (
        f"now_naive()={actual.isoformat()} 偏离 utc+8h (expected {expected.isoformat()}) {delta}s"
    )


def test_now_naive_ignores_system_timezone() -> None:
    """即使运行环境 TZ 不是 Asia/Shanghai，helper 也输出 Shanghai 时间。"""
    from core.time import SHANGHAI_TZ

    aware = datetime.now(SHANGHAI_TZ)
    naive = now_naive()
    # aware - 8h == naive（naive 是 aware 去掉 tzinfo）
    expected_naive = aware.replace(tzinfo=None)
    delta = abs((naive - expected_naive).total_seconds())
    assert delta < 1.0


def test_shanghai_tz_constant() -> None:
    """SHANGHAI_TZ 显式构造 UTC+8。"""
    assert SHANGHAI_TZ.utcoffset(None) == timedelta(hours=8)
    assert SHANGHAI_TZ.tzname(None) == "Asia/Shanghai"


def test_now_shanghai_iso_format() -> None:
    """ISO 字符串 + 'Z' 后缀（前端 toLocaleTimeString 解析）。"""
    s = now_shanghai_iso()
    # 格式: YYYY-MM-DDTHH:MM:SSZ
    assert len(s) == 20
    assert s[4] == "-"
    assert s[7] == "-"
    assert s[10] == "T"
    assert s[13] == ":"
    assert s[16] == ":"
    assert s.endswith("Z")
    # 时间值校验：能 round-trip 解析回 aware datetime
    parsed = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    # parsed + 8h == 上海当前 wall clock（allow ±1s）
    expected = datetime.now(timezone.utc) + timedelta(hours=8)
    delta = abs((parsed.astimezone(timezone.utc) - expected).total_seconds())
    assert delta < 1.0
