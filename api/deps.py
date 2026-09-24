"""2026-09-24 重构：MCP 域（`/api/mcp/*` + `/mcp` mount）整体下线。

本仓 v1 DI 已收紧至 STS 凭证端口 + 打印端口（2026-09-24 PR-2 新增）；
MCP AI 只读入口对应源文件已删除，业务 AI 只读查询改由 backend-rust v2 的
`/api/v2/*` 承接。

活跃注入：

- `get_session`                       — 请求级 Session（commit/rollback + dashboard
                                     广播调度）
- `get_sts_service`                   — `api/v1/sts.py`（STS 临时凭证端口）
- `get_printing_service`              — `api/v1/printing.py`（零件标签 PDF，
                                     2026-09-24 PR-2 新增）
- `get_delivery_note_print_service`   — `api/v1/delivery_note_print.py`（送货单
                                     / 标签 Excel，2026-09-24 PR-2 新增）

业务路由整体由 backend-rust v2 承接；本仓仅承担 STS 凭证签发 + 打印端点
（均与 IAM 无关）。
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from repository import (
    AssemblyRepository,
    CustomerRepository,
    DeliveryNoteRepository,
    PartBatchRepository,
    PartFileRepository,
    PartRepository,
)
from service import (
    DeliveryNotePrintService,
    PrintingServiceFacade,
    StsService,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """请求级 Session。正常返回时自动 commit，异常时回滚。

    commit 成功后把累积的 dashboard 广播「脱离 session」调度到后台任务
    （见 `_schedule_dashboard_flush`）——广播读到的是**已提交**的最新状态，
    且**不阻塞** HTTP 响应：慢 WS 客户端不会再拖慢本请求返回。
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        else:
            _drain_and_schedule_dashboard_broadcasts(session)


# session.info 键：在请求事务内累积待广播的 dashboard 消息，
# 由 get_session 在 commit 成功后「脱离 session」调度到后台任务。
_SNAPSHOT_PENDING_KEY = "dashboard_snapshot_pending"
_EVENTS_PENDING_KEY = "dashboard_events_pending"

# 后台单飞（single-flight）调度状态：
# - 最多一个在跑的 flush task（强引用挂在 _flush_task 上，避免被 GC）；
# - 多个并发请求的 snapshot 标志 / event 队列合并到模块级 pending，
#   worker 一轮 drain 后若又有新 pending 则继续循环；
# - 避免写入风暴触发大量并发 snapshot session 抢连接池。
_pending_snapshot = False
_pending_events: list[tuple[str, dict]] = []
_flush_task: "asyncio.Task[None] | None" = None

logger = logging.getLogger(__name__)


def _drain_and_schedule_dashboard_broadcasts(session: AsyncSession) -> None:
    """commit 成功后：同步把 session.info 里的待广播数据取出（脱离 session），
    再调度后台 flush。**不 await**，因此不拖慢 HTTP 响应。

    payload 都是普通 dict、event 是 (str, dict) 元组，拷成模块级纯 Python 值后
    与请求 session 生命周期完全解耦。
    """
    try:
        info = session.info
        want_snapshot = bool(info.pop(_SNAPSHOT_PENDING_KEY, False))
        events = info.pop(_EVENTS_PENDING_KEY, None) or []
    except Exception:  # noqa: BLE001
        return
    if not want_snapshot and not events:
        return
    _schedule_dashboard_flush(want_snapshot, list(events))


def _schedule_dashboard_flush(
    want_snapshot: bool,
    events: list[tuple[str, dict]],
) -> None:
    """把待广播合并进模块级 pending，并保证只有一个后台 flush task 在跑。"""
    global _pending_snapshot, _flush_task
    if want_snapshot:
        _pending_snapshot = True
    if events:
        _pending_events.extend(events)
    if not _pending_snapshot and not _pending_events:
        return
    if _flush_task is not None and not _flush_task.done():
        # 已有 worker 在跑：它会在下一轮循环里 drain 到刚合并进来的 pending
        return
    _flush_task = asyncio.create_task(_dashboard_flush_worker())


async def _dashboard_flush_worker() -> None:
    """后台 flush：先推 snapshot，再逐条推 event（保持「先 snapshot 后 event」）。

    2026-09-24：dashboard WS 路由 (`/ws/dashboard`) 与 `service.dashboard` 已下线，
    广播器在生产路径上不可用——`api.v1.ws` 已迁移至历史归档，本 worker 走
    try/except ImportError 软失败；测试环境 conftest 已把 `api.v1.ws` 注册为
    DormantStub（patch 后 import 仍能拿到 mocked 名字，测试 `test_dashboard_
    broadcast_scheduling.py` 继续生效）。
    """
    global _pending_snapshot, _pending_events
    while _pending_snapshot or _pending_events:
        want_snapshot = _pending_snapshot
        events = _pending_events
        _pending_snapshot = False
        _pending_events = []
        try:
            # 2026-09-24：dashboard WS 路由下线；`api.v1.ws` 已不在生产 import
            # 图中，软失败 + logger.debug 让本 worker 不抛异常。conftest 测试
            # 环境仍注入 DormantStub，patch 后可正常解析。
            from api.v1.ws import (
                broadcast_dashboard_event,
                broadcast_dashboard_snapshot,
            )

            if want_snapshot:
                await broadcast_dashboard_snapshot()
            for event_type, payload in events:
                await broadcast_dashboard_event(event_type, payload)
        except ImportError:
            # 2026-09-24：dashboard 域下线，广播器在生产不可用；仅 debug 不 warn。
            logger.debug("dashboard broadcast skipped: api.v1.ws not importable")
        except Exception:
            logger.exception("dashboard broadcast flush failed")


# ============================================================
# STS 临时凭证端口（2026-09-17 新增）
# ============================================================
# 裸开鉴权（参考历史 /api/mcp/* 模式），靠部署层 nginx / 安全组隔离。
# 不注入 `get_session`（无 DB IO）/ `get_current_user`（bypass 模式无关）。
def get_sts_service() -> "StsService":
    """STS 临时凭证 service 工厂。

    service 层只读 settings + 调 `core.sts.grant_sts_tmp_key`，无状态；
    直接返回单例即可，不放 Depends 链上避免和 SessionInit 冲突。

    类型注解用字符串字面量避免在文件顶部导入 `service.sts.StsService`
    触发 service 链导入。
    """
    return StsService()


# ============================================================
# 打印端口（2026-09-24 PR-2 新增）
# ============================================================
# 与 STS 同款裸开鉴权（参考 `/api/v1/files/sts-*`），安全性靠部署层 nginx 隔离。
def get_printing_service(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PrintingServiceFacade:
    """2026-09-24 PR-2 新增：零件标签 PDF 打印 service 工厂。

    ``api/v1/printing.py`` 单件 / 批量两个端点共享一个 facade 实例（handler 内
    按 part_ids / assembly_ids 维度分流）。
    """
    return PrintingServiceFacade(
        parts=PartRepository(session),
        part_files=PartFileRepository(session),
        assemblies=AssemblyRepository(session),
    )


def get_delivery_note_print_service(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DeliveryNotePrintService:
    """2026-09-24 PR-2 新增：送货单 / 标签 Excel service 工厂。

    ``api/v1/delivery_note_print.py`` 送货单 / 标签两个端点共用：
    - notes：404 校验
    - customers：L1 客户前缀解析 + leaf/parent 客户名映射
    - part_batches：拉 note 关联批次（标签 / 送货单行构建共享）
    - assemblies：2026-09-24 PR-2 改为 service 内部组装，API 层不传 ORM
    """
    return DeliveryNotePrintService(
        notes=DeliveryNoteRepository(session),
        parts=PartRepository(session),
        customers=CustomerRepository(session),
        part_batches=PartBatchRepository(session),
        assemblies=AssemblyRepository(session),
    )
