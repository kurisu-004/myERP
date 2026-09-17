"""2026-09-17 重构：v1 业务路由下线 + JWT bypass 后，DI 工厂只保留 4 类
注入：

- `get_session`              — 请求级 Session（commit/rollback + dashboard
                              广播调度）
- `get_auth_service` / `get_user_service`
                            — `api/v1/auth.py` (login / refresh / me /
                              change-password)
- `get_sts_service`          — `api/v1/sts.py` (STS 临时凭证端口)
- `get_mcp_query_service` / `get_mcp_part_file_service`
                            — `/api/mcp/*`（AI 只读入口）

所有 v1 业务 router 的 DI 工厂（get_part_service / get_assembly_service /
...）已整体移除；业务由 backend-rust v2 承接。
"""
import asyncio
from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from core.permission import CurrentUser, get_current_user
from repository import (
    AssemblyRepository,
    CustomerRepository,
    MenuRepository,
    OutsourceCompanyRepository,
    PartBatchRepository,
    PartEventRepository,
    PartFileRepository,
    PartRepository,
    ProcessRepository,
    ShelfRepository,
    UserRepository,
    UserRoleRepository,
    WorkerRepository,
)
from service import (
    AuthService,
    McpQueryService,
    PartFileService,
    StsService,
    UserService,
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
    want_snapshot: bool, events: list[tuple[str, dict]],
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

    - 一轮 drain 当前 pending → 广播（await WS send，可能被慢客户端拖住，但只影响
      本后台任务，不影响任何 HTTP 响应）；
    - drain 期间新到的 pending 会让 worker 继续下一轮，收敛后退出；
    - snapshot 构建走 broadcast_dashboard_snapshot 自己的 SessionLocal，
      **不引用**任何请求 session。
    - 捕获并 log 所有异常，避免 "Task exception was never retrieved"。
    """
    global _pending_snapshot, _pending_events
    import logging

    logger = logging.getLogger(__name__)
    while _pending_snapshot or _pending_events:
        want_snapshot = _pending_snapshot
        events = _pending_events
        _pending_snapshot = False
        _pending_events = []
        try:
            if want_snapshot:
                # 2026-09-17：dashboard WS 路由下线（v1 业务），但广播器仍可
                # 被外部脚本触发（保留兼容）；懒加载避免强制引用 _archive/api_v1/ws。
                from api.v1.ws import broadcast_dashboard_event, broadcast_dashboard_snapshot

                if want_snapshot:
                    await broadcast_dashboard_snapshot()
                for event_type, payload in events:
                    await broadcast_dashboard_event(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("dashboard broadcast flush failed")


# ============================================================
# 仓库 DI（仅保留 v1 auth + MCP 仍引用的）
# ============================================================
def get_user_repo(
    session: AsyncSession = Depends(get_session),
) -> UserRepository:
    return UserRepository(session)


def get_user_role_repo(
    session: AsyncSession = Depends(get_session),
) -> UserRoleRepository:
    return UserRoleRepository(session)


def get_shelf_repo(
    session: AsyncSession = Depends(get_session),
) -> ShelfRepository:
    return ShelfRepository(session)


def get_menu_repo(
    session: AsyncSession = Depends(get_session),
) -> MenuRepository:
    return MenuRepository(session)


# ============================================================
# Auth / User service DI（api/v1/auth.py 用）
# ============================================================
def get_auth_service(
    users: UserRepository = Depends(get_user_repo),
    user_roles: UserRoleRepository = Depends(get_user_role_repo),
    shelves: ShelfRepository = Depends(get_shelf_repo),
    menus: MenuRepository = Depends(get_menu_repo),
) -> AuthService:
    return AuthService(
        users=users, user_roles=user_roles, shelves=shelves, menus=menus,
    )


def get_user_service(
    users: UserRepository = Depends(get_user_repo),
    user_roles: UserRoleRepository = Depends(get_user_role_repo),
    shelves: ShelfRepository = Depends(get_shelf_repo),
    user: CurrentUser = Depends(get_current_user),
) -> UserService:
    return UserService(
        users=users, user_roles=user_roles, shelves=shelves, current_user=user,
    )


# ============================================================
# STS 临时凭证端口（2026-09-17 新增）
# ============================================================
# 裸开鉴权（参考 /api/mcp/* 模式），靠部署层 nginx / 安全组隔离。
# 不注入 `get_session`（无 DB IO）/ `get_current_user`（bypass 模式无关）。
def get_sts_service() -> "StsService":
    """STS 临时凭证 service 工厂。

    service 层只读 settings + 调 `core.sts.grant_sts_tmp_key`，无状态；
    直接返回单例即可，不放 Depends 链上避免和 SessionInit 冲突。

    类型注解用字符串字面量避免在文件顶部导入 `service.sts.StsService`
    触发 service 链导入（service/__init__.py 还会触发一堆 v1 已下线
    service 的 init，被一并移到 `_archive/` 后才不会 import）。
    """
    return StsService()


# ============================================================
# MCP 只读查询（免鉴权）
# ============================================================
# ⚠️ 以下两个工厂**刻意不注入 `get_current_user`**——`/api/mcp/*` 是给 AI 用的
# 免登录只读入口，注入了会让端点直接 401。安全性靠部署层（nginx / 安全组不暴露
# `/api/mcp` 与 `/mcp` 前缀）保证，不要在这里加回 `Depends(get_current_user)`。
def get_mcp_query_service(
    session: AsyncSession = Depends(get_session),
) -> McpQueryService:
    """MCP 只读查询 service 工厂（无 current_user，无 broadcaster）。"""
    return McpQueryService(
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        outsource_companies=OutsourceCompanyRepository(session),
        assemblies=AssemblyRepository(session),
        files=PartFileRepository(session),
        events=PartEventRepository(session),
    )


def get_mcp_part_file_service(
    session: AsyncSession = Depends(get_session),
) -> PartFileService:
    """MCP 图纸代理用的文件 service（`current_user=None`，仅走读路径）。"""
    return PartFileService(files=PartFileRepository(session))