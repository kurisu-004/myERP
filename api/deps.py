import asyncio
from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from core.permission import CurrentUser, get_current_user
from repository import (
    ApplicantRepository,
    AssemblyRepository,
    CustomerRepository,
    DeliveryNoteCounterRepository,
    DeliveryNoteEventRepository,
    DeliveryNoteRepository,
    MenuRepository,
    OutsourceCompanyProcessRepository,
    OutsourceCompanyRepository,
    OutsourceQuoteEventRepository,
    OutsourceQuoteRepository,
    OutsourceShipmentRepository,
    PartBatchRepository,
    PartEventRepository,
    PartFileRepository,
    PartRepository,
    ProcessRepository,
    SerialCounterRepository,
    ShelfProcessRepository,
    ShelfRepository,
    UserRepository,
    UserRoleRepository,
    WorkerRepository,
    WorkTypeProcessRepository,
    WorkTypeRepository,
)
from service import (
    ApplicantService,
    AssemblyService,
    AuthService,
    CustomerService,
    DeliveryNoteService,
    OutsourceCompanyService,
    OutsourceQuoteService,
    PartFileService,
    PartService,
    ProcessService,
    ShelfProcessService,
    ShelfService,
    UserService,
    WorkerService,
    WorkTypeProcessService,
    WorkTypeService,
)
from service.part import (
    Broadcaster,
    EventBroadcaster,
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

    from api.v1.ws import broadcast_dashboard_event, broadcast_dashboard_snapshot

    logger = logging.getLogger(__name__)
    while _pending_snapshot or _pending_events:
        want_snapshot = _pending_snapshot
        events = _pending_events
        _pending_snapshot = False
        _pending_events = []
        try:
            if want_snapshot:
                await broadcast_dashboard_snapshot()
            for event_type, payload in events:
                await broadcast_dashboard_event(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("dashboard broadcast flush failed")


def get_serial_counter_repo(
    session: AsyncSession = Depends(get_session),
) -> SerialCounterRepository:
    return SerialCounterRepository(session)


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


def get_part_repository(
    session: AsyncSession = Depends(get_session),
) -> PartRepository:
    """图纸打印 / 打印 service 共用的 PartRepository 工厂。

    2026-07-10 起也被 `get_shelf_service` 引用（list_for_return 拿 current_load），
    故前置到此避免模块加载顺序问题。
    """
    return PartRepository(session)


def get_part_event_repository(
    session: AsyncSession = Depends(get_session),
) -> PartEventRepository:
    """2026-07-28 新增：供 `get_outsource_company_service` 对账端点用。"""
    return PartEventRepository(session)


def get_process_repo(
    session: AsyncSession = Depends(get_session),
) -> ProcessRepository:
    """2026-07-10 起被 `get_shelf_service` 引用（list_for_return 校验 next_process_id），
    故前置到此避免模块加载顺序问题。
    """
    return ProcessRepository(session)


def get_shelf_process_repo(
    session: AsyncSession = Depends(get_session),
) -> ShelfProcessRepository:
    """2026-07-10 起被 `get_shelf_service` 引用（list_for_return 取 mapped process codes），
    故前置到此避免模块加载顺序问题。
    """
    return ShelfProcessRepository(session)


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


def get_shelf_service(
    shelves: ShelfRepository = Depends(get_shelf_repo),
    user_roles: UserRoleRepository = Depends(get_user_role_repo),
    parts: PartRepository = Depends(get_part_repository),
    processes: ProcessRepository = Depends(get_process_repo),
    shelf_process: ShelfProcessRepository = Depends(get_shelf_process_repo),
    user: CurrentUser = Depends(get_current_user),
) -> ShelfService:
    """注入 ShelfService；2026-07-10 起 list_for_return 需要 parts/processes/
    shelf_process 三个 repo（共享 HMI RETURN picker）。CRUD 流用不到，但
    注入是 cheap（无 IO），不区分。
    """
    return ShelfService(
        shelves=shelves,
        user_roles=user_roles,
        parts=parts,
        processes=processes,
        shelf_process=shelf_process,
        current_user=user,
    )


def get_part_service(
    session: AsyncSession = Depends(get_session),
    serial_counters: SerialCounterRepository = Depends(get_serial_counter_repo),
    user: CurrentUser = Depends(get_current_user),
) -> PartService:
    """注入 PartService，并把 dashboard 广播器作为闭包传入。

    闭包内自带独立 SessionLocal，不复用请求 session（请求 session 此时已经
    commit/rollback，避免在事件触发瞬间读到不一致的数据）。

    同时注入两个闭包：
    - `_broadcaster()`：触发整张 snapshot 立即重推；
    - `_event_broadcaster(event_type, payload)`：触发单条业务事件推送
      （PICKED_UP / RELEASED / PLACED_ON_SHELF），由前端横幅组件消费。

    2026-07-10 起：注入 `PartFileRepository` 以支持
    `POST /parts/batch` multipart 端点的 PDF 上传 + 下发前置校验
    (≥1 G_CODE + ≥1 SETUP_SHEET)。

    2026-07-16 起：注入 `OutsourceQuoteRepository` + `OutsourceQuoteEventRepository`
    以支持 send_to_outsource 防御闸 + APPROVED→USED 自动 mark。
    """

    async def _broadcaster() -> None:
        # 不立即广播：累积到 session.info，由 get_session 在事务 commit 成功后
        # 统一 flush（见 _flush_dashboard_broadcasts）。否则独立 session 构建的
        # 快照读不到本请求尚未提交的写入 → 大屏货架「没变化」。
        session.info[_SNAPSHOT_PENDING_KEY] = True

    async def _event_broadcaster(event_type: str, payload: dict) -> None:
        session.info.setdefault(_EVENTS_PENDING_KEY, []).append((event_type, payload))

    return PartService(
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),  # 2026-07-29：批次化
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        events=PartEventRepository(session),
        serial_counters=serial_counters,
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        work_types=WorkTypeRepository(session),
        work_type_process=WorkTypeProcessRepository(session),
        applicants=ApplicantRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
        files=PartFileRepository(session),
        assemblies=AssemblyRepository(session),  # 2026-07-21：create_parts_tree 写 t_assembly
        delivery_notes_repo=DeliveryNoteRepository(session),  # 2026-07-22：PR-G 详情显示所属送货单
        outsource_companies=OutsourceCompanyRepository(session),
        outsource_company_process=OutsourceCompanyProcessRepository(session),
        outsource_quotes=OutsourceQuoteRepository(session),
        quote_events=OutsourceQuoteEventRepository(session),
        outsource_shipments=OutsourceShipmentRepository(session),  # 2026-07-30：外协发货记录
        broadcaster=_broadcaster,
        event_broadcaster=_event_broadcaster,
        current_user=user,
    )


def get_worker_service(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> WorkerService:
    return WorkerService(
        workers=WorkerRepository(session),
        work_types=WorkTypeRepository(session),
        current_user=user,
    )


# ============================================================
# 工种 / 工序 / 映射 DI
# ============================================================
def get_work_type_repo(
    session: AsyncSession = Depends(get_session),
) -> WorkTypeRepository:
    return WorkTypeRepository(session)


def get_work_type_process_repo(
    session: AsyncSession = Depends(get_session),
) -> WorkTypeProcessRepository:
    return WorkTypeProcessRepository(session)


def get_shelf_process_service(
    shelves: ShelfRepository = Depends(get_shelf_repo),
    processes: ProcessRepository = Depends(get_process_repo),
    junction: ShelfProcessRepository = Depends(get_shelf_process_repo),
    user: CurrentUser = Depends(get_current_user),
) -> ShelfProcessService:
    return ShelfProcessService(
        shelves=shelves, processes=processes, junction=junction,
        current_user=user,
    )


def get_work_type_service(
    session: AsyncSession = Depends(get_session),
    work_types: WorkTypeRepository = Depends(get_work_type_repo),
    junction: WorkTypeProcessRepository = Depends(get_work_type_process_repo),
    user: CurrentUser = Depends(get_current_user),
) -> WorkTypeService:
    """注入 WorkTypeService；worker_repo 用于软删前引用校验。

    直接构造 WorkerRepository（共享 session），避免 DI 循环依赖。
    """
    return WorkTypeService(
        work_types=work_types,
        worker_repo=WorkerRepository(session),
        junction_repo=junction,
        current_user=user,
    )


def get_process_service(
    processes: ProcessRepository = Depends(get_process_repo),
    junction: WorkTypeProcessRepository = Depends(get_work_type_process_repo),
    user: CurrentUser = Depends(get_current_user),
) -> ProcessService:
    return ProcessService(
        processes=processes,
        junction_repo=junction,
        current_user=user,
    )


def get_work_type_process_service(
    work_types: WorkTypeRepository = Depends(get_work_type_repo),
    processes: ProcessRepository = Depends(get_process_repo),
    junction: WorkTypeProcessRepository = Depends(get_work_type_process_repo),
    user: CurrentUser = Depends(get_current_user),
) -> WorkTypeProcessService:
    return WorkTypeProcessService(
        work_types=work_types,
        processes=processes,
        junction=junction,
        current_user=user,
    )


def get_customer_service(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> CustomerService:
    return CustomerService(
        customers=CustomerRepository(session),
        parts=PartRepository(session),
        assemblies=AssemblyRepository(session),
        current_user=user,
    )


def get_applicant_repo(
    session: AsyncSession = Depends(get_session),
) -> ApplicantRepository:
    return ApplicantRepository(session)


def get_applicant_service(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> ApplicantService:
    return ApplicantService(
        applicants=ApplicantRepository(session),
        customers=CustomerRepository(session),
        parts=PartRepository(session),
        current_user=user,
    )


def get_part_file_repository(
    session: AsyncSession = Depends(get_session),
) -> PartFileRepository:
    """统一文件仓储工厂（零件 / 装配体图纸 + G 代码 + 设定单）。"""
    return PartFileRepository(session)


def get_part_file_service(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> PartFileService:
    """统一文件 service 工厂。"""
    return PartFileService(
        files=PartFileRepository(session),
        current_user=user,
    )


def get_part_repository(
    session: AsyncSession = Depends(get_session),
) -> PartRepository:
    """图纸打印 / 打印 service 共用的 PartRepository 工厂。"""
    return PartRepository(session)


def get_assembly_repo(
    session: AsyncSession = Depends(get_session),
) -> AssemblyRepository:
    """装配件 Repository 工厂。"""
    return AssemblyRepository(session)


def get_assembly_service(
    session: AsyncSession = Depends(get_session),
    serial_counters: SerialCounterRepository = Depends(get_serial_counter_repo),
    user: CurrentUser = Depends(get_current_user),
) -> AssemblyService:
    """注入 AssemblyService。

    共享同一 session/事务：构造 PartService / PartFileService 时复用 session，
    装配体创建时的所有 DB 写入都在一个事务里，任一失败整体回滚。
    """
    parts_repo = PartRepository(session)
    files_repo = PartFileRepository(session)
    assemblies_repo = AssemblyRepository(session)
    customers_repo = CustomerRepository(session)
    workers_repo = WorkerRepository(session)
    events_repo = PartEventRepository(session)
    shelves_repo = ShelfRepository(session)

    part_service = PartService(
        parts=parts_repo,
        customers=customers_repo,
        workers=workers_repo,
        events=events_repo,
        serial_counters=serial_counters,
        shelves=shelves_repo,
        processes=ProcessRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
        files=files_repo,
        delivery_notes_repo=DeliveryNoteRepository(session),  # 2026-07-22：PR-G
        outsource_companies=OutsourceCompanyRepository(session),
        outsource_company_process=OutsourceCompanyProcessRepository(session),
        outsource_shipments=OutsourceShipmentRepository(session),  # 2026-07-30：cancel 级联取消子件时关闭开放发货单
        part_batches=PartBatchRepository(session),  # 2026-07-29：批次化 — cancel / create_root_batch 必填
        current_user=user,
    )
    part_files = PartFileService(
        files=files_repo,
        current_user=user,
    )

    async def _broadcaster() -> None:
        # 装配体级联取消 / 软删会让 dashboard 卡片消失，走整张 snapshot 重推。
        # 同 PartService：累积到 session.info，commit 成功后由 get_session flush。
        session.info[_SNAPSHOT_PENDING_KEY] = True

    async def _event_broadcaster(event_type: str, payload: dict) -> None:
        session.info.setdefault(_EVENTS_PENDING_KEY, []).append((event_type, payload))

    return AssemblyService(
        assemblies=assemblies_repo,
        parts=parts_repo,
        files=files_repo,
        customers=customers_repo,
        serial_counters=serial_counters,
        events=events_repo,
        part_service=part_service,
        part_files=part_files,
        applicants=ApplicantRepository(session),
        broadcaster=_broadcaster,
        event_broadcaster=_event_broadcaster,
        current_user=user,
    )


def get_delivery_note_service(
    session: AsyncSession = Depends(get_session),
    broadcaster: Broadcaster | None = None,
    event_broadcaster: EventBroadcaster | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> DeliveryNoteService:
    """送货单管理 service 工厂（PR-G 2026-07-22 替代 PR-B 老 XLSX 导出）。

    注入：
    - notes / note_events / counter：CRUD + 事件流 + 每日单号
    - parts / part_events：pickup() 时联动 part.deliver + 写 TPartEvent
    - customers：建单校验存在
    - workers：pickup() 校验司机工种 / 活跃
    - broadcaster / event_broadcaster：pickup() 影响多个 part 状态，触发整张
      dashboard snapshot 与业务事件（DELIVERY_NOTE_PICKED_UP）；通过闭包传，
      不复用请求 session。

    调用方 API 层用 require_roles 守权限（MANAGER/CLERK 编辑；pickup 任意已登录）。
    """
    async def _broadcaster() -> None:
        # pickup() 影响多个 part.deliver → dashboard 卡片「待送货」消失，
        # 走整张 snapshot 重推。同 PartService：累积到 session.info，
        # commit 成功后由 get_session flush。
        session.info[_SNAPSHOT_PENDING_KEY] = True

    async def _event_broadcaster(event_type: str, payload: dict) -> None:
        session.info.setdefault(_EVENTS_PENDING_KEY, []).append(
            (event_type, payload),
        )

    return DeliveryNoteService(
        session=session,
        notes=DeliveryNoteRepository(session),
        note_events=DeliveryNoteEventRepository(session),
        counter=DeliveryNoteCounterRepository(session),
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),  # 2026-07-29：批次化
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        work_types=WorkTypeRepository(session),
        part_events=PartEventRepository(session),
        assemblies=AssemblyRepository(session),  # 2026-08-03：pickup 触发装配件 rollup
        broadcaster=_broadcaster if broadcaster is None else broadcaster,
        event_broadcaster=(
            _event_broadcaster if event_broadcaster is None
            else event_broadcaster
        ),
        current_user=user,
    )


# ============================================================
# 外协公司 DI（2026-07-15 新增）
# ============================================================
def get_outsource_company_repo(
    session: AsyncSession = Depends(get_session),
) -> OutsourceCompanyRepository:
    return OutsourceCompanyRepository(session)


def get_outsource_company_process_repo(
    session: AsyncSession = Depends(get_session),
) -> OutsourceCompanyProcessRepository:
    return OutsourceCompanyProcessRepository(session)


def get_outsource_shipment_repo(
    session: AsyncSession = Depends(get_session),
) -> OutsourceShipmentRepository:
    return OutsourceShipmentRepository(session)


def get_outsource_company_service(
    companies: OutsourceCompanyRepository = Depends(get_outsource_company_repo),
    junction: OutsourceCompanyProcessRepository = Depends(
        get_outsource_company_process_repo,
    ),
    processes: ProcessRepository = Depends(get_process_repo),
    part_repo: PartRepository = Depends(get_part_repository),
    part_events: PartEventRepository = Depends(get_part_event_repository),
    shipments: OutsourceShipmentRepository = Depends(get_outsource_shipment_repo),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> OutsourceCompanyService:
    return OutsourceCompanyService(
        companies=companies, junction=junction, processes=processes,
        part_repo=part_repo, part_events=part_events,
        # 2026-07-30：对账页改为基于 t_outsource_shipment
        outsource_shipments=shipments,
        customers=CustomerRepository(session),
        current_user=user,
    )


# ============================================================
# 外协报价 DI（2026-07-16 新增）
# ============================================================
def get_outsource_quote_repo(
    session: AsyncSession = Depends(get_session),
) -> OutsourceQuoteRepository:
    return OutsourceQuoteRepository(session)


def get_outsource_quote_event_repo(
    session: AsyncSession = Depends(get_session),
) -> OutsourceQuoteEventRepository:
    return OutsourceQuoteEventRepository(session)


def get_outsource_quote_service(
    quotes: OutsourceQuoteRepository = Depends(get_outsource_quote_repo),
    quote_events: OutsourceQuoteEventRepository = Depends(
        get_outsource_quote_event_repo,
    ),
    parts: PartRepository = Depends(get_part_repository),
    companies: OutsourceCompanyRepository = Depends(get_outsource_company_repo),
    processes: ProcessRepository = Depends(get_process_repo),
    shipments: OutsourceShipmentRepository = Depends(get_outsource_shipment_repo),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> OutsourceQuoteService:
    return OutsourceQuoteService(
        quotes=quotes,
        quote_events=quote_events,
        parts=parts,
        companies=companies,
        processes=processes,
        customers=CustomerRepository(session),
        shelves=ShelfRepository(session),
        workers=WorkerRepository(session),
        part_events=PartEventRepository(session),
        shipments=shipments,
        current_user=user,
    )
