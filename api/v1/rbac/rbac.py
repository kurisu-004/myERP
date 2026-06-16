"""RBAC 管理 API: 用户/角色/权限 CRUD.

所有端点都要求 superadmin 权限 (require_superadmin).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi import Response
from pydantic import BaseModel

from api.deps import get_uow
from api.rbac_deps import require_superadmin
from model import TUser
from repository.unit_of_work import UnitOfWork
from schema.rbac import (
    PermissionCreateIn,
    PermissionOut,
    PermissionUpdateIn,
    RoleCreateIn,
    RoleOut,
    RolePage,
    RoleUpdateIn,
    UserCreateIn,
    UserDetailOut,
    UserOut,
    UserPage,
    UserUpdateIn,
)
from service.rbac.rbac_service import RBACService

router = APIRouter(
    prefix="/rbac",
    tags=["权限管理(仅超管)"],
    dependencies=[Depends(require_superadmin())],
)


def get_rbac_service(uow: UnitOfWork = Depends(get_uow)) -> RBACService:
    return RBACService(uow)


# ============================================================
#  User
# ============================================================
@router.get("/users", response_model=UserPage, summary="分页查询用户")
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=50),
    svc: RBACService = Depends(get_rbac_service),
) -> UserPage:
    return await svc.list_users(page=page, size=size, keyword=keyword)


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="新建用户",
)
async def create_user(
    payload: UserCreateIn,
    svc: RBACService = Depends(get_rbac_service),
) -> UserOut:
    return await svc.create_user(
        employee_no=payload.employee_no,
        username=payload.username,
        name=payload.name,
        password=payload.password,
        role_ids=payload.role_ids,
    )


@router.get("/users/{user_id}", response_model=UserDetailOut, summary="用户详情(带角色)")
async def get_user(
    user_id: int,
    svc: RBACService = Depends(get_rbac_service),
) -> UserDetailOut:
    return await svc.get_user_detail(user_id)


@router.put("/users/{user_id}", response_model=UserOut, summary="更新用户")
async def update_user(
    user_id: int,
    payload: UserUpdateIn,
    svc: RBACService = Depends(get_rbac_service),
) -> UserOut:
    return await svc.update_user(
        user_id=user_id,
        name=payload.name,
        password=payload.password,
        is_active=payload.is_active,
        role_ids=payload.role_ids,
    )


@router.delete("/users/{user_id}", summary="删除用户(软删)", status_code=200)
async def delete_user(
    user_id: int,
    svc: RBACService = Depends(get_rbac_service),
) -> dict[str, str]:
    await svc.delete_user(user_id)
    return {"message": "ok"}


# ============================================================
#  Role
# ============================================================
@router.get("/roles", response_model=RolePage, summary="分页查询角色")
async def list_roles(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=50),
    svc: RBACService = Depends(get_rbac_service),
) -> RolePage:
    return await svc.list_roles(page=page, size=size, keyword=keyword)


@router.get("/roles/all", response_model=list[RoleOut], summary="角色全量列表(下拉用)")
async def list_all_roles(
    svc: RBACService = Depends(get_rbac_service),
) -> list[RoleOut]:
    return await svc.list_all_roles()


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    summary="新建角色(并授权)",
)
async def create_role(
    payload: RoleCreateIn,
    svc: RBACService = Depends(get_rbac_service),
) -> RoleOut:
    return await svc.create_role(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        permission_ids=payload.permission_ids,
    )


@router.put("/roles/{role_id}", response_model=RoleOut, summary="更新角色(并替换权限)")
async def update_role(
    role_id: int,
    payload: RoleUpdateIn,
    svc: RBACService = Depends(get_rbac_service),
) -> RoleOut:
    return await svc.update_role(
        role_id=role_id,
        name=payload.name,
        description=payload.description,
        permission_ids=payload.permission_ids,
    )


@router.delete("/roles/{role_id}", summary="删除角色(内置不可删)", status_code=200)
async def delete_role(
    role_id: int,
    svc: RBACService = Depends(get_rbac_service),
) -> dict[str, str]:
    await svc.delete_role(role_id)
    return {"message": "ok"}


# ============================================================
#  Permission
# ============================================================
@router.get("/permissions", response_model=list[PermissionOut], summary="权限列表(含禁用的)")
async def list_permissions(
    svc: RBACService = Depends(get_rbac_service),
) -> list[PermissionOut]:
    return await svc.list_permissions()


@router.post(
    "/permissions",
    response_model=PermissionOut,
    status_code=status.HTTP_201_CREATED,
    summary="新建权限点",
)
async def create_permission(
    payload: PermissionCreateIn,
    svc: RBACService = Depends(get_rbac_service),
) -> PermissionOut:
    return await svc.create_permission(
        code=payload.code,
        name=payload.name,
        type=payload.type,
        parent_id=payload.parent_id,
        path=payload.path,
        icon=payload.icon,
        component=payload.component,
        sort_order=payload.sort_order,
        is_enabled=payload.is_enabled,
    )


@router.put(
    "/permissions/{permission_id}",
    response_model=PermissionOut,
    summary="更新权限点",
)
async def update_permission(
    permission_id: int,
    payload: PermissionUpdateIn,
    svc: RBACService = Depends(get_rbac_service),
) -> PermissionOut:
    return await svc.update_permission(
        permission_id=permission_id,
        name=payload.name,
        parent_id=payload.parent_id,
        path=payload.path,
        icon=payload.icon,
        component=payload.component,
        sort_order=payload.sort_order,
        is_enabled=payload.is_enabled,
    )


# ============================================================
#  Assign
# ============================================================
class RoleIdsIn(BaseModel):
    role_ids: list[int]


@router.put(
    "/users/{user_id}/roles",
    response_model=UserDetailOut,
    summary="为用户分配角色(替换式)",
)
async def assign_user_roles(
    user_id: int,
    payload: RoleIdsIn,
    svc: RBACService = Depends(get_rbac_service),
) -> UserDetailOut:
    await svc.update_user(user_id=user_id, role_ids=payload.role_ids)
    return await svc.get_user_detail(user_id)
