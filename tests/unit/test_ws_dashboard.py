"""Unit tests for `api/v1/ws.py::ws_dashboard`.

锁定 token 失效/客户端中途断开两种情况下，handler 不能把异常往外冒——
否则 uvicorn 的 ``run_asgi`` 会把它当 ``BaseException`` 记成 "Exception
in ASGI application"，前端 token 过期后 dashboard.ts 自动重连每跑一次
就刷一行 ERROR。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect, status

from api.v1.ws import (
    DASHBOARD_CHANNEL,
    EVENTS_CHANNEL,
    ConnectionManager,
    _handle_subscription_message,
    manager,
    ws_dashboard,
)


pytestmark = pytest.mark.asyncio


# ======================================================================
# Tests
# ======================================================================


async def test_invalid_token_rejects_without_raising():
    """token 无效 → 直接 ``ws.close(1008)``，handler 不能抛。"""
    sent: list[dict] = []

    async def _close(code: int = 1000, reason: str | None = None) -> None:
        sent.append({"type": "websocket.close", "code": code, "reason": reason or ""})

    ws = MagicMock(spec=WebSocket)
    ws.client_state = MagicMock(CONNECTING=0, CONNECTED=1)
    ws.client_state = 0  # 还没收到 connect
    ws.close = AsyncMock(side_effect=_close)

    with patch("api.v1.ws._resolve_user_from_ws", AsyncMock(return_value=None)):
        # 关键断言：handler 不能向外抛任何异常
        await ws_dashboard(ws, token="invalid-token")

    # 必须发了一个 close 帧
    assert sent == [{"type": "websocket.close", "code": status.WS_1008_POLICY_VIOLATION, "reason": ""}]


async def test_client_disconnected_before_handler_runs():
    """客户端已经断了 → ``ws.close`` 触发 OSError → handler 必须吃掉。

    模拟 uvicorn 在 ``asgi_send`` 里抛 ``ClientDisconnected``
    （``uvicorn.protocols.utils.ClientDisconnected`` 是 ``OSError`` 子类），
    旧实现里这个 OSError 经 Starlette 转成 ``WebSocketDisconnect`` 冒泡
    出 handler，被 uvicorn ``run_asgi`` 当 ``BaseException`` 刷 ERROR。
    """
    client_disconnected = OSError("Connection reset by peer")

    async def _close(code: int = 1000, reason: str | None = None) -> None:
        raise client_disconnected

    ws = MagicMock(spec=WebSocket)
    ws.client_state = MagicMock(CONNECTING=0, CONNECTED=1)
    ws.client_state = 0
    ws.close = AsyncMock(side_effect=_close)

    with patch("api.v1.ws._resolve_user_from_ws", AsyncMock(return_value=None)):
        # 旧实现会抛；新实现必须静默吞掉
        await ws_dashboard(ws, token="invalid")


async def test_client_disconnected_after_handshake_raises_starlette_wsdisconnect():
    """回归测试：用 Starlette 真对象确认 close→send 抛 OSError 时，
    handler 也能吞掉，并把 ``WebSocketDisconnect(code=1006)`` 重抛也算覆盖。
    """
    from starlette.websockets import WebSocket as StarletteWS

    captured: list[dict] = []

    class FakeSend:
        async def __call__(self, msg):
            captured.append(msg)
            # 模拟 uvicorn 的 asgi_send 在握手完成后遇到对端重置
            if msg["type"] == "websocket.close":
                raise ConnectionError("peer gone")

    class FakeReceive:
        def __init__(self):
            self.i = 0

        async def __call__(self):
            # 给出 connect，让 accept 把它转到 CONNECTED
            self.i += 1
            if self.i == 1:
                return {"type": "websocket.connect"}
            return {"type": "websocket.disconnect", "code": 1006}

    rec, snd = FakeReceive(), FakeSend()
    st_ws = StarletteWS(
        scope={
            "type": "websocket",
            "headers": [],
            "query_string": b"",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
        },
        receive=rec,
        send=snd,
    )
    # 显式 accept 把 client_state 推进到 CONNECTED，模拟"已经 accept 再 close"路径
    await st_ws.accept()

    with patch("api.v1.ws._resolve_user_from_ws", AsyncMock(return_value=None)):
        # 即使走 accept 路径（client_state=CONNECTED），close 触发 ConnectionError
        # 仍要被吃掉。注意这里 Starlette 把 ConnectionError 当作 OSError 在
        # CONNECTED 分支捕获并转成 WebSocketDisconnect，handler 必须兜住。
        await ws_dashboard(st_ws, token="invalid")

    # 应该至少发过一个 close 帧
    close_frames = [m for m in captured if m["type"] == "websocket.close"]
    assert close_frames, f"expected at least one close frame, got {captured}"


async def test_valid_token_path_is_unaffected():
    """保证修复不破坏正常路径：token 有效 → 走 manager.connect。

    用真 ``SessionLocal`` 太重，直接 patch 掉 ``manager.connect`` +
    ``build_snapshot_with_workers`` 让 handler 在接受后就抛个可控异常退出。
    """
    ws = MagicMock(spec=WebSocket)
    ws.client_state = MagicMock(CONNECTING=0, CONNECTED=1)
    ws.client_state = 0
    ws.send = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1006))

    fake_manager = MagicMock()
    fake_manager.connect = AsyncMock()
    fake_manager.disconnect = MagicMock()

    with patch("api.v1.ws._resolve_user_from_ws", AsyncMock(return_value=42)), \
         patch("api.v1.ws.manager", fake_manager), \
         patch("api.v1.ws.build_snapshot_with_workers", AsyncMock(return_value={})), \
         patch("api.v1.ws._snapshot_payload", lambda d: "{}"):
        await ws_dashboard(ws, token="valid-token")

    fake_manager.connect.assert_awaited_once_with(ws)
    fake_manager.disconnect.assert_called_once_with(ws)


async def test_subscription_resume_sends_private_snapshot():
    """Dashboard subscribe/resume only requests a snapshot for that client."""
    ws = MagicMock(spec=WebSocket)
    fake_manager = MagicMock()
    fake_manager.subscribe.return_value = True

    with patch("api.v1.ws.manager", fake_manager), \
         patch("api.v1.ws._build_and_send_snapshot", AsyncMock()) as send_snapshot:
        await _handle_subscription_message(
            ws, '{"type":"subscribe","channel":"dashboard"}'
        )

    fake_manager.subscribe.assert_called_once_with(ws, DASHBOARD_CHANNEL)
    send_snapshot.assert_awaited_once_with(ws)


async def test_repeated_dashboard_subscribe_does_not_duplicate_snapshot():
    """A duplicate control frame does not cause another sync request."""
    ws = MagicMock(spec=WebSocket)
    fake_manager = MagicMock()
    fake_manager.subscribe.return_value = False

    with patch("api.v1.ws.manager", fake_manager), \
         patch("api.v1.ws._build_and_send_snapshot", AsyncMock()) as send_snapshot:
        await _handle_subscription_message(
            ws, '{"type":"subscribe","channel":"dashboard"}'
        )

    send_snapshot.assert_not_awaited()


async def test_dashboard_and_event_broadcasts_are_channel_scoped():
    """Dashboard unsubscribe stops snapshots while the event channel remains active."""
    local_manager = ConnectionManager()
    dashboard_ws = MagicMock(spec=WebSocket)
    event_ws = MagicMock(spec=WebSocket)
    dashboard_ws.accept = AsyncMock()
    event_ws.accept = AsyncMock()
    dashboard_ws.send_text = AsyncMock()
    event_ws.send_text = AsyncMock()

    await local_manager.connect(dashboard_ws)
    await local_manager.connect(event_ws)
    local_manager.subscribe(dashboard_ws, DASHBOARD_CHANNEL)
    local_manager.subscribe(event_ws, EVENTS_CHANNEL)

    await local_manager.broadcast("snapshot", DASHBOARD_CHANNEL)
    await local_manager.broadcast("event", EVENTS_CHANNEL)

    dashboard_ws.send_text.assert_awaited_once_with("snapshot")
    event_ws.send_text.assert_awaited_once_with("event")

    local_manager.unsubscribe(dashboard_ws, DASHBOARD_CHANNEL)
    dashboard_ws.send_text.reset_mock()
    event_ws.send_text.reset_mock()

    await local_manager.broadcast("snapshot-2", DASHBOARD_CHANNEL)
    await local_manager.broadcast("event-2", EVENTS_CHANNEL)

    dashboard_ws.send_text.assert_not_awaited()
    event_ws.send_text.assert_awaited_once_with("event-2")


async def test_ws_connection_waits_for_subscribe_before_snapshot():
    """A connected socket receives no initial snapshot until it subscribes."""
    ws = MagicMock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(
        side_effect=[
            '{"type":"subscribe","channel":"dashboard"}',
            WebSocketDisconnect(code=1006),
        ]
    )

    manager.active.clear()
    manager._subscriptions.clear()
    with patch("api.v1.ws._resolve_user_from_ws", AsyncMock(return_value=42)), \
         patch("api.v1.ws._build_and_send_snapshot", AsyncMock()) as send_snapshot:
        await ws_dashboard(ws, token="valid-token")

    send_snapshot.assert_awaited_once_with(ws)
    assert ws.send_text.await_count == 0
    assert ws not in manager.active
