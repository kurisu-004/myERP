"""异步集成测试 fixtures.

策略：
- session 级别：连接测试库、建表（一次）
- function 级别：每个测试在 SAVEPOINT 里跑，结束回滚
"""
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from model import Base, TPermission, TRole, TRolePermission, TUser, TUserRole
from repository import (
    OrderRepository,
    PartRepository,
    PermissionRepository,
    RolePermissionRepository,
    RoleRepository,
    UserRepository,
    UserRoleRepository,
    WorkerRepository,
)
from utils.id_gen import new_id
from utils.password import hash_password

TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:991027@127.0.0.1:5433/mydb_test"
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


# -------- session 级别：建表 --------
@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


# -------- function 级别：独立事务 + 回滚 --------
@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = TestSessionLocal(bind=conn)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


# -------- repository fixtures --------
@pytest_asyncio.fixture
def user_repo(db: AsyncSession) -> UserRepository:
    return UserRepository(db)


@pytest_asyncio.fixture
def order_repo(db: AsyncSession) -> OrderRepository:
    return OrderRepository(db)


@pytest_asyncio.fixture
def part_repo(db: AsyncSession) -> PartRepository:
    return PartRepository(db)


@pytest_asyncio.fixture
def worker_repo(db: AsyncSession) -> WorkerRepository:
    return WorkerRepository(db)


@pytest_asyncio.fixture
def role_repo(db: AsyncSession) -> RoleRepository:
    return RoleRepository(db)


@pytest_asyncio.fixture
def permission_repo(db: AsyncSession) -> PermissionRepository:
    return PermissionRepository(db)


@pytest_asyncio.fixture
def user_role_repo(db: AsyncSession) -> UserRoleRepository:
    return UserRoleRepository(db)


@pytest_asyncio.fixture
def role_permission_repo(db: AsyncSession) -> RolePermissionRepository:
    return RolePermissionRepository(db)


# -------- factory fixtures --------
@pytest_asyncio.fixture
def user_factory(db: AsyncSession, user_repo: UserRepository):
    async def _create(
        username: str = "u_test",
        *,
        employee_no: str | None = None,
        name: str | None = None,
        password_hash: str | None = None,
        is_active: int = 1,
    ) -> TUser:
        if employee_no is None:
            employee_no = f"U{username[:17]:<17}".strip()
        return await user_repo.create(
            TUser(
                id=new_id(),
                employee_no=employee_no,
                username=username,
                name=name or username,
                password_hash=password_hash or hash_password("test_pass"),
                is_active=is_active,
            )
        )

    return _create


@pytest_asyncio.fixture
def worker_factory(db: AsyncSession, worker_repo: WorkerRepository):
    from model import TWorker

    async def _create(name: str | None = None) -> TWorker:
        if name is None:
            name = f"w_{id(object())}"
        return await worker_repo.create(TWorker(name=name))

    return _create


@pytest_asyncio.fixture
def order_factory(db: AsyncSession, order_repo: OrderRepository):
    from decimal import Decimal

    from model import TOrder

    async def _create(user_id: int, price: Decimal = Decimal("100.00")) -> TOrder:
        return await order_repo.create(TOrder(user_id=user_id, price=price))

    return _create


@pytest_asyncio.fixture
def part_factory(db: AsyncSession, part_repo: PartRepository):
    from model import TPart
    from model.enums import PartStatus

    async def _create(
        order_id: int,
        name: str = "part",
        status: PartStatus = PartStatus.PENDING,
        worker_id: int | None = None,
    ) -> TPart:
        return await part_repo.create(
            TPart(order_id=order_id, name=name, status=status, worker_id=worker_id)
        )

    return _create


@pytest_asyncio.fixture
def role_factory(db: AsyncSession, role_repo: RoleRepository):
    async def _create(
        code: str = "ROLE_TEST",
        *,
        name: str | None = None,
        description: str | None = None,
        is_builtin: int = 0,
    ) -> TRole:
        return await role_repo.create(
            TRole(
                id=new_id(),
                code=code,
                name=name or code,
                description=description,
                is_builtin=is_builtin,
            )
        )

    return _create


@pytest_asyncio.fixture
def permission_factory(db: AsyncSession, permission_repo: PermissionRepository):
    async def _create(
        code: str = "test:menu",
        *,
        name: str | None = None,
        type: str = "MENU",
        parent_id: int | None = None,
        path: str | None = None,
        icon: str | None = None,
        component: str | None = None,
        sort_order: int = 0,
        is_enabled: int = 1,
    ) -> TPermission:
        return await permission_repo.create(
            TPermission(
                id=new_id(),
                code=code,
                name=name or code,
                type=type,
                parent_id=parent_id,
                path=path,
                icon=icon,
                component=component,
                sort_order=sort_order,
                is_enabled=is_enabled,
            )
        )

    return _create


@pytest_asyncio.fixture
def user_role_factory(db: AsyncSession, user_role_repo: UserRoleRepository):
    async def _create(user_id: int, role_id: int) -> TUserRole:
        return await user_role_repo.create(
            TUserRole(id=new_id(), user_id=user_id, role_id=role_id)
        )

    return _create


@pytest_asyncio.fixture
def role_permission_factory(
    db: AsyncSession, role_permission_repo: RolePermissionRepository
):
    async def _create(role_id: int, permission_id: int) -> TRolePermission:
        return await role_permission_repo.create(
            TRolePermission(id=new_id(), role_id=role_id, permission_id=permission_id)
        )

    return _create
