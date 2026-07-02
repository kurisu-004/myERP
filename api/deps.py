from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from repository import (
    AssemblyRepository,
    CustomerRepository,
    DrawingFileRepository,
    MenuRepository,
    PartEventRepository,
    PartRepository,
    SerialCounterRepository,
    ShelfRepository,
    UserRepository,
    UserRoleRepository,
    WorkerRepository,
)
from service import (
    AssemblyService,
    AuthService,
    CustomerService,
    DrawingService,
    PartService,
    ShelfService,
    UserService,
    WorkerService,
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


def get_auth_service(
    users: UserRepository = Depends(get_user_repo),
    user_roles: UserRoleRepository = Depends(get_user_role_repo),
    shelves: ShelfRepository = Depends(get_shelf_repo),
    menus: MenuRepository = Depends(get_menu_repo),
) -> AuthService:
    return AuthService(
        users=users, user_roles=user_roles, shelves=shelves, menus=menus
    )


def get_user_service(
    users: UserRepository = Depends(get_user_repo),
    user_roles: UserRoleRepository = Depends(get_user_role_repo),
    shelves: ShelfRepository = Depends(get_shelf_repo),
) -> UserService:
    return UserService(users=users, user_roles=user_roles, shelves=shelves)


def get_shelf_service(
    shelves: ShelfRepository = Depends(get_shelf_repo),
    user_roles: UserRoleRepository = Depends(get_user_role_repo),
) -> ShelfService:
    return ShelfService(shelves=shelves, user_roles=user_roles)


def get_part_service(
    session: AsyncSession = Depends(get_session),
    serial_counters: SerialCounterRepository = Depends(get_serial_counter_repo),
) -> PartService:
    """注入 PartService，并把 dashboard 广播器作为闭包传入。

    闭包内自带独立 SessionLocal，不复用请求 session（请求 session 此时已经
    commit/rollback，避免在事件触发瞬间读到不一致的数据）。

    同时注入两个闭包：
    - `_broadcaster()`：触发整张 snapshot 立即重推；
    - `_event_broadcaster(event_type, payload)`：触发单条业务事件推送
      （PICKED_UP / RELEASED / PLACED_ON_SHELF），由前端横幅组件消费。
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
        broadcaster=_broadcaster,
        event_broadcaster=_event_broadcaster,
    )


def get_worker_service(
    session: AsyncSession = Depends(get_session),
) -> WorkerService:
    return WorkerService(workers=WorkerRepository(session))


def get_customer_service(
    session: AsyncSession = Depends(get_session),
) -> CustomerService:
    return CustomerService(customers=CustomerRepository(session))


def get_drawing_service(
    session: AsyncSession = Depends(get_session),
) -> DrawingService:
    return DrawingService(
        files=DrawingFileRepository(session),
        parts=PartRepository(session),
        assemblies=AssemblyRepository(session),
    )


def get_assembly_service(
    session: AsyncSession = Depends(get_session),
    serial_counters: SerialCounterRepository = Depends(get_serial_counter_repo),
) -> AssemblyService:
    """注入 AssemblyService。

    共享同一 session/事务：构造 PartService / DrawingService 时复用 session，
    装配体创建时的所有 DB 写入都在一个事务里，任一失败整体回滚。
    """
    from api.v1.ws import broadcast_dashboard_event

    parts_repo = PartRepository(session)
    files_repo = DrawingFileRepository(session)
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
    )
    drawings = DrawingService(
        files=files_repo, parts=parts_repo, assemblies=assemblies_repo
    )

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
        drawings=drawings,
        event_broadcaster=_event_broadcaster,
    )
