"""Unit tests for service/printing.py delivery-date buffer helper.

2026-07-30 引入：打印背面 / 扫码台 / 大屏展示用交期需要比真实工单交期提前
3 天，留给物流/分厂流转余量。后端打印层在 _PartPrintData 装配处统一应用
缓冲，业务查询不感知。

覆盖：
- 基本减法（8 月 5 日 -3 天 = 8 月 2 日）
- None 输入透传
- 跨月（8 月 2 日 -3 天 = 7 月 30 日）
- 常量值
"""
from __future__ import annotations

from datetime import date

from service.printing import (
    DELIVERY_DATE_BUFFER_DAYS,
    _buffered_delivery_date,
)


def test_buffer_subtracts_three_days():
    """基本减法：8/5 - 3 = 8/2（buffer 是减法不是加法）。"""
    assert _buffered_delivery_date(date(2026, 8, 5)) == date(2026, 8, 2)


def test_buffer_none_passthrough():
    """None 必须透传，不得抛异常也不得退回 today()-3。"""
    assert _buffered_delivery_date(None) is None


def test_buffer_crosses_month_boundary():
    """跨月减法验证：8/2 - 3 = 7/30（date 会正确处理负 day 回退）。"""
    assert _buffered_delivery_date(date(2026, 8, 2)) == date(2026, 7, 30)


def test_buffer_constant_value():
    """常量为 3（与任务约定一致），改值会破业务必须主动审查。"""
    assert DELIVERY_DATE_BUFFER_DAYS == 3
