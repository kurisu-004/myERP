"""看板广播「非阻塞后台调度」单测（2026-07-20 重构）。

覆盖 api/deps.py 的 fire-and-forget 调度：
- commit 后从 session.info 取出待广播数据并调度后台 task（不 await）；
- 空 info 不调度；
- single-flight：连续调度只挂一个 task（合并 pending）；
- 后台异常被吞掉并 log，不冒泡；
- 后台 worker 不引用请求 session（snapshot 走自己的 SessionLocal）。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.deps as deps

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """每个用例前后复位模块级单飞状态，避免相互串扰。"""
    deps._pending_snapshot = False
    deps._pending_events = []
    deps._flush_task = None
    yield
    t = deps._flush_task
    if t is not None and not t.done():
        t.cancel()
    deps._pending_snapshot = False
    deps._pending_events = []
    deps._flush_task = None


async def _drain_task() -> None:
    t = deps._flush_task
    if t is not None:
        await t


async def test_drain_pops_info_and_schedules_background_flush():
    with patch("api.v1.ws.broadcast_dashboard_snapshot", new=AsyncMock()) as snap, \
         patch("api.v1.ws.broadcast_dashboard_event", new=AsyncMock()) as evt:
        session = MagicMock()
        session.info = {
            deps._SNAPSHOT_PENDING_KEY: True,
            deps._EVENTS_PENDING_KEY: [("PICKED_UP", {"x": 1})],
        }
        deps._drain_and_schedule_dashboard_broadcasts(session)

        # 待广播数据已从 session.info 取出（脱离 session）
        assert deps._SNAPSHOT_PENDING_KEY not in session.info
        assert deps._EVENTS_PENDING_KEY not in session.info
        # 已调度后台 task（drain 本身同步返回、不 await 广播）
        assert deps._flush_task is not None

        await _drain_task()
        snap.assert_awaited_once()
        evt.assert_awaited_once_with("PICKED_UP", {"x": 1})


async def test_empty_info_schedules_nothing():
    session = MagicMock()
    session.info = {}
    deps._drain_and_schedule_dashboard_broadcasts(session)
    assert deps._flush_task is None


async def test_single_flight_one_task_for_concurrent_schedules():
    with patch("api.v1.ws.broadcast_dashboard_snapshot", new=AsyncMock()), \
         patch("api.v1.ws.broadcast_dashboard_event", new=AsyncMock()):
        # 两次调度之间不让出事件循环 → worker 尚未启动 → 第二次不新建 task
        deps._schedule_dashboard_flush(True, [])
        t1 = deps._flush_task
        deps._schedule_dashboard_flush(True, [("RELEASED", {})])
        t2 = deps._flush_task
        assert t1 is t2
        await _drain_task()


async def test_background_exception_is_swallowed():
    with patch(
        "api.v1.ws.broadcast_dashboard_snapshot",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ), patch("api.v1.ws.broadcast_dashboard_event", new=AsyncMock()):
        session = MagicMock()
        session.info = {deps._SNAPSHOT_PENDING_KEY: True}
        deps._drain_and_schedule_dashboard_broadcasts(session)
        # 后台异常不应冒泡；await task 正常完成
        await _drain_task()
        assert deps._pending_snapshot is False


async def test_worker_does_not_touch_request_session():
    """后台 worker 只调用 broadcast_*（各自 SessionLocal），不碰请求 session。"""
    with patch("api.v1.ws.broadcast_dashboard_snapshot", new=AsyncMock()), \
         patch("api.v1.ws.broadcast_dashboard_event", new=AsyncMock()):
        session = MagicMock()
        session.info = {deps._SNAPSHOT_PENDING_KEY: True}
        deps._drain_and_schedule_dashboard_broadcasts(session)
        await _drain_task()
        # drain 后只 pop 过 info，未对 session 发起任何 execute/commit 等调用
        session.execute.assert_not_called()
        session.commit.assert_not_called()
