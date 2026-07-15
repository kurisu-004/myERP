from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from core.permission import CurrentUser, get_current_user
from repository import (
    ApplicantRepository,
    AssemblyRepository,
    CustomerRepository,
    MenuRepository,
    OutsourceCompanyProcessRepository,
    OutsourceCompanyRepository,
    OutsourceQuoteEventRepository,
    OutsourceQuoteRepository,
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


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """请求级 Session。正常返回时自动 commit，异常时回滚。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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
    from api.v1.ws import broadcast_dashboard_event, broadcast_dashboard_snapshot

    async def _broadcaster() -> None:
        await broadcast_dashboard_snapshot()

    async def _event_broadcaster(event_type: str, payload: dict) -> None:
        await broadcast_dashboard_event(event_type, payload)

    return PartService(
        parts=PartRepository(session),
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
        outsource_companies=OutsourceCompanyRepository(session),
        outsource_company_process=OutsourceCompanyProcessRepository(session),
        outsource_quotes=OutsourceQuoteRepository(session),
        quote_events=OutsourceQuoteEventRepository(session),
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


def get_assembly_service(
    session: AsyncSession = Depends(get_session),
    serial_counters: SerialCounterRepository = Depends(get_serial_counter_repo),
    user: CurrentUser = Depends(get_current_user),
) -> AssemblyService:
    """注入 AssemblyService。

    共享同一 session/事务：构造 PartService / PartFileService 时复用 session，
    装配体创建时的所有 DB 写入都在一个事务里，任一失败整体回滚。
    """
    from api.v1.ws import broadcast_dashboard_event, broadcast_dashboard_snapshot

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
        outsource_companies=OutsourceCompanyRepository(session),
        outsource_company_process=OutsourceCompanyProcessRepository(session),
        current_user=user,
    )
    part_files = PartFileService(
        files=files_repo,
        current_user=user,
    )

    async def _broadcaster() -> None:
        # 装配体级联取消 / 软删会让 dashboard 卡片消失，
        # 走与 PartService 同样的整张 snapshot 重推路径。
        await broadcast_dashboard_snapshot()

    async def _event_broadcaster(event_type: str, payload: dict) -> None:
        await broadcast_dashboard_event(event_type, payload)

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
) -> DeliveryNoteService:
    """送货单 Excel 导出 service 工厂（PR-B 2026-07-10）。

    只需要 PartRepository（按 id 批量查）+ CustomerRepository（按 id 批量查父/子
    客户名拼路径）。无 user 依赖，调用方 API 层再做权限校验。
    """
    return DeliveryNoteService(
        parts=PartRepository(session),
        customers=CustomerRepository(session),
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


def get_outsource_company_service(
    companies: OutsourceCompanyRepository = Depends(get_outsource_company_repo),
    junction: OutsourceCompanyProcessRepository = Depends(
        get_outsource_company_process_repo,
    ),
    processes: ProcessRepository = Depends(get_process_repo),
    user: CurrentUser = Depends(get_current_user),
) -> OutsourceCompanyService:
    return OutsourceCompanyService(
        companies=companies, junction=junction, processes=processes,
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
        current_user=user,
    )
