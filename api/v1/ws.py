"""WebSocket：大屏数据实时推送。

- /ws/dashboard：连接成功立即推一次快照（ready_queue + in_process）；
  由 lifespan 启动的后台任务每 5 秒再推一次。
- 业务侧（service 层）状态变更成功后调用 `broadcast_dashboard_snapshot`
  触发立即推送，不等 5 秒周期。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.database import SessionLocal
from core.dashboard import build_snapshot_with_workers

logger = logging.getLogger(__name__)

router = APIRouter()

PUSH_INTERVAL_SEC = 5.0


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
    """取一次快照并广播。供 lifespan 周期任务和 service 触发调用。"""
    try:
        async with SessionLocal() as session:
            data = await build_snapshot_with_workers(session)
        await manager.broadcast(_snapshot_payload(data))
    except Exception:
        logger.exception("dashboard snapshot/broadcast failed")


@router.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        # 1) 连接即推：首屏立即有数据
        async with SessionLocal() as session:
            data = await build_snapshot_with_workers(session)
        await ws.send_text(_snapshot_payload(data))

        # 2) 保持连接。客户端可以发任意文本当心跳；服务端不依赖它，
        #    但需要持续 read 才能让 close 立即被检测到。
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
    """后台任务：每 PUSH_INTERVAL_SEC 秒向所有 dashboard 客户端广播一次。"""
    while True:
        await asyncio.sleep(PUSH_INTERVAL_SEC)
        if not manager.active:
            continue
        await _build_and_broadcast()


# ============================================================
# 供 service 层注入的"业务触发"回调
# ============================================================
async def broadcast_dashboard_snapshot() -> None:
    """service 层在状态变更成功后调这个，触发立即推送。

    由 `api.deps.get_part_service` 把它包成闭包注入到 PartService.broadcaster。
    """
    if not manager.active:
        return
    await _build_and_broadcast()


def _event_payload(event_type: str, data: dict) -> str:
    """包装业务事件消息（区别于 snapshot 周期推送）。

    信封与 snapshot 一致，仅 `type` 和 `event_type` 不同；前端按 type 分发。
    """
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
    """service 层在 PICKED_UP / RELEASED 等关键动作后调这个。

    由 `api.deps.get_part_service` 包成闭包注入到
    `PartService.event_broadcaster`，触发立即推送一条 event 消息给所有
    dashboard 客户端。**无活跃连接时静默 no-op**，不报错。
    """
    if not manager.active:
        return
    await manager.broadcast(_event_payload(event_type, payload))