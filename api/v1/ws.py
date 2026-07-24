"""WebSocket：大屏数据实时推送。

- /ws/dashboard：连接成功后等待客户端订阅 Dashboard 频道，再推一次快照。
- 业务侧（service 层）状态变更成功后调用 `broadcast_dashboard_snapshot`
  触发立即推送；通知横幅走 `broadcast_dashboard_event`。

权限：连接时必须带有效 JWT（`?token=...` 或 `Authorization: Bearer ...`），
任意已登录用户（MANAGER / SHELF_ACCOUNT）可订阅。
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from core.database import SessionLocal
from core.exception import BizError
from core.security import decode_access_token
from core.time import now_shanghai_iso
from model import TUser
from service.dashboard import build_snapshot_with_workers
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter()

DASHBOARD_CHANNEL = "dashboard"
EVENTS_CHANNEL = "events"
SubscriptionChannel = Literal["dashboard", "events"]


async def _resolve_user_from_ws(ws: WebSocket, token: str | None) -> int | None:
    """从 WS query/header 取 token，校验有效；返回 user_id 或 None。"""
    if not token:
        # fallback: header
        auth = ws.headers.get("authorization") or ws.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except BizError:
        return None
    except (KeyError, ValueError, TypeError):
        return None
    async with SessionLocal() as session:
        user = (await session.execute(select(TUser).where(TUser.id == user_id))).scalar_one_or_none()
    if user is None or user.deleted_at is not None or not user.is_active:
        return None
    return user_id


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        self._subscriptions[ws] = set()
        logger.info("ws dashboard connected, total=%d", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)
            self._subscriptions.pop(ws, None)
            logger.info("ws dashboard disconnected, total=%d", len(self.active))

    def subscribe(self, ws: WebSocket, channel: SubscriptionChannel) -> bool:
        if ws not in self.active:
            return False
        subscriptions = self._subscriptions.setdefault(ws, set())
        if channel in subscriptions:
            return False
        subscriptions.add(channel)
        return True

    def unsubscribe(self, ws: WebSocket, channel: SubscriptionChannel) -> None:
        self._subscriptions.get(ws, set()).discard(channel)

    def has_subscribers(self, channel: SubscriptionChannel) -> bool:
        return any(channel in self._subscriptions.get(ws, set()) for ws in self.active)

    def is_subscribed(self, ws: WebSocket, channel: SubscriptionChannel) -> bool:
        return ws in self.active and channel in self._subscriptions.get(ws, set())

    async def send(self, ws: WebSocket, message: str, channel: SubscriptionChannel) -> None:
        if not self.is_subscribed(ws, channel):
            return
        try:
            await ws.send_text(message)
        except Exception:
            self.disconnect(ws)

    async def broadcast(self, message: str, channel: SubscriptionChannel) -> None:
        if not self.has_subscribers(channel):
            return
        dead: list[WebSocket] = []
        for ws in list(self.active):
            if not self.is_subscribed(ws, channel):
                continue
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


def _snapshot_payload(data: dict) -> str:
    return json.dumps(
        {
            "type": "snapshot",
            "data": data,
            "ts": now_shanghai_iso(),
        },
        ensure_ascii=False,
    )


async def _build_and_broadcast() -> None:
    try:
        async with SessionLocal() as session:
            data = await build_snapshot_with_workers(session)
        await manager.broadcast(_snapshot_payload(data), DASHBOARD_CHANNEL)
    except Exception:
        logger.exception("dashboard snapshot/broadcast failed")


async def _build_and_send_snapshot(ws: WebSocket) -> None:
    try:
        async with SessionLocal() as session:
            data = await build_snapshot_with_workers(session)
        await manager.send(ws, _snapshot_payload(data), DASHBOARD_CHANNEL)
    except Exception:
        logger.exception("dashboard snapshot/send failed")


async def _handle_subscription_message(ws: WebSocket, raw_message: str) -> None:
    try:
        message = json.loads(raw_message)
    except (TypeError, json.JSONDecodeError):
        logger.warning("ignoring invalid dashboard WS control message")
        return
    if not isinstance(message, dict):
        return

    message_type = message.get("type")
    channel = message.get("channel")
    if channel not in (DASHBOARD_CHANNEL, EVENTS_CHANNEL):
        logger.warning("ignoring dashboard WS control message with invalid channel: %r", channel)
        return

    if message_type == "subscribe":
        changed = manager.subscribe(ws, channel)
        if changed and channel == DASHBOARD_CHANNEL:
            # 订阅恢复后只给当前客户端补一份最新快照，不打扰其他连接。
            await _build_and_send_snapshot(ws)
    elif message_type == "unsubscribe":
        manager.unsubscribe(ws, channel)


@router.websocket("/ws/dashboard")
async def ws_dashboard(
    ws: WebSocket,
    token: str | None = Query(default=None, description="Bearer JWT（也可走 Authorization header）"),
) -> None:
    user_id = await _resolve_user_from_ws(ws, token)
    if user_id is None:
        # 拒绝无效 token 的连接。
        #
        # 直接发送 ``websocket.close`` 帧（ASGI spec 允许在握手前 close），
        # 不走 "先 accept 再 close"——后者在客户端中途断开时会让 uvicorn
        # 在 ``asgi_send`` 里抛 ``ClientDisconnected``（OSError 子类）。
        # Starlette 在 ``WebSocket.send()`` 的 CONNECTED 分支把 OSError
        # 转成 ``WebSocketDisconnect(code=1006)`` 重抛，在 CONNECTING
        # 分支则直接冒泡；这两种异常都会逃出本 handler，被 uvicorn
        # ``run_asgi`` 当 ``BaseException`` 记成 "Exception in ASGI
        # application"。前端 token 过期后 dashboard.ts 的自动重连每
        # 跑一次就在后端刷一行 ERROR——这里统一吃掉。
        try:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        except (WebSocketDisconnect, RuntimeError, OSError):
            # 客户端已经在我们之前断开（ASGI 层 ``ClientDisconnected``
            # 是 ``OSError`` 子类）；uvicorn 会按 HTTP 403 / RST 正常
            # 收尾，不应继续往上抛。
            pass
        logger.info("ws dashboard rejected: missing/invalid token")
        return
    await manager.connect(ws)
    try:
        # 连接本身保持着，但只有收到 subscribe/dashboard 后才发送快照。
        while True:
            await _handle_subscription_message(ws, await ws.receive_text())
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws dashboard error")
    finally:
        manager.disconnect(ws)


async def broadcast_dashboard_snapshot() -> None:
    if not manager.has_subscribers(DASHBOARD_CHANNEL):
        return
    await _build_and_broadcast()


def _event_payload(event_type: str, data: dict) -> str:
    return json.dumps(
        {
            "type": "event",
            "event_type": event_type,
            "data": data,
            "ts": now_shanghai_iso(),
        },
        ensure_ascii=False,
    )


async def broadcast_dashboard_event(event_type: str, payload: dict) -> None:
    if not manager.has_subscribers(EVENTS_CHANNEL):
        return
    await manager.broadcast(_event_payload(event_type, payload), EVENTS_CHANNEL)
