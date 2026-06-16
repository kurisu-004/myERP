"""RBAC service tests fixtures: 提供 service 依赖."""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from repository.rbac import (
    PermissionRepository,
    RolePermissionRepository,
    RoleRepository,
    UserRoleRepository,
)
from repository.unit_of_work import UnitOfWork
from repository.user import UserRepository
from service.rbac.auth_service import AuthService
from service.rbac.rbac_service import RBACService


@pytest_asyncio.fixture
def uow(db: AsyncSession) -> UnitOfWork:
    return UnitOfWork(
        session=db,
        users=UserRepository(db),
        orders=None,  # type: ignore[arg-type]
        parts=None,  # type: ignore[arg-type]
        workers=None,  # type: ignore[arg-type]
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        user_roles=UserRoleRepository(db),
        role_permissions=RolePermissionRepository(db),
    )


@pytest_asyncio.fixture
def auth_service(uow: UnitOfWork) -> AuthService:
    return AuthService(uow)


@pytest_asyncio.fixture
def rbac_service(uow: UnitOfWork) -> RBACService:
    return RBACService(uow)
