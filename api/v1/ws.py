"""WebSocket：大屏数据实时推送。

- /ws/dashboard：连接成功立即推一次快照（ready_queue + in_process）；
  由 lifespan 启动的后台任务每 5 秒再推一次。
- 业务侧（service 层）状态变更成功后调用 `broadcast_dashboard_snapshot`
  触发立即推送，不等 5 秒周期。

权限：连接时必须带有效 JWT（`?token=...` 或 `Authorization: Bearer ...`），
任意已登录用户（MANAGER / SHELF_ACCOUNT）可订阅。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status as http_status

from core.database import SessionLocal
from core.dashboard import build_snapshot_with_workers
from core.error_code import ErrCode
from core.exception import BizError
from core.security import decode_access_token
from model import TUser
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter()

PUSH_INTERVAL_SEC = 5.0


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

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        logger.info("ws dashboard connected, total=%d", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)
            logger.info("ws dashboard disconnected, total=%d", len(self.active))

    async def broadcast(self, message: str) -> None:
        if not self.active:
            return
        dead: list[WebSocket] = []
        for ws in self.active:
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
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        },
        ensure_ascii=False,
    )


async def _build_and_broadcast() -> None:
    try:
        async with SessionLocal() as session:
            data = await build_snapshot_with_workers(session)
        await manager.broadcast(_snapshot_payload(data))
    except Exception:
        logger.exception("dashboard snapshot/broadcast failed")


@router.websocket("/ws/dashboard")
async def ws_dashboard(
    ws: WebSocket,
    token: str | None = Query(default=None, description="Bearer JWT（也可走 Authorization header）"),
) -> None:
    user_id = await _resolve_user_from_ws(ws, token)
    if user_id is None:
        # 接受后立即关闭，避免泄露任何数据
        await ws.accept()
        await ws.close(code=status.http.WS_1008_POLICY_VIOLATION)
        logger.info("ws dashboard rejected: missing/invalid token")
        return
    await manager.connect(ws)
    try:
        # 1) 连接即推：首屏立即有数据
        async with SessionLocal() as session:
            data = await build_snapshot_with_workers(session)
        await ws.send_text(_snapshot_payload(data))

        # 2) 保持连接
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws dashboard error")
    finally:
        manager.disconnect(ws)


async def dashboard_push_loop() -> None:
    while True:
        await asyncio.sleep(PUSH_INTERVAL_SEC)
        if not manager.active:
            continue
        await _build_and_broadcast()


async def broadcast_dashboard_snapshot() -> None:
    if not manager.active:
        return
    await _build_and_broadcast()


def _event_payload(event_type: str, data: dict) -> str:
    return json.dumps(
        {
            "type": "event",
            "event_type": event_type,
            "data": data,
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        },
        ensure_ascii=False,
    )


async def broadcast_dashboard_event(event_type: str, payload: dict) -> None:
    if not manager.active:
        return
    await manager.broadcast(_event_payload(event_type, payload))