"""RBAC API 集成测试 fixtures."""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_uow
from main import app
from repository.unit_of_work import UnitOfWork


@pytest_asyncio.fixture
async def uow(db: AsyncSession) -> UnitOfWork:
    """在测试 db session 上构造完整 UoW, 供测试代码手动操作.

    注意: db 已在 SAVEPOINT 中,所有改动会在测试结束时回滚.
    """
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

    return UnitOfWork(
        session=db,
        users=UserRepository(db),
        orders=OrderRepository(db),
        parts=PartRepository(db),
        workers=WorkerRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        user_roles=UserRoleRepository(db),
        role_permissions=RolePermissionRepository(db),
    )


@pytest_asyncio.fixture
async def client(db: AsyncSession, uow: UnitOfWork) -> AsyncClient:
    """覆盖 get_uow,让所有 API 端点使用测试 session (含 rbac repositories)."""

    async def _override_get_uow() -> UnitOfWork:
        return uow

    app.dependency_overrides[get_uow] = _override_get_uow
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
