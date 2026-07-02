"""账号管理端点（MANAGER-only）。"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_user_service
from core.permission import require_role
from model.enums import UserRole
from schema.user import (
    UserAddRoleRequest,
    UserCreateRequest,
    UserListOut,
    UserListQuery,
    UserOut,
    UserRoleOut,
    UserUpdateRequest,
)
from service.user import UserService


router = APIRouter(
    prefix="/users",
    tags=["账号管理"],
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)


@router.get("", response_model=UserListOut, summary="账号列表")
async def list_users(
    username_like: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: UserService = Depends(get_user_service),
) -> UserListOut:
    return await svc.list_users(
        UserListQuery(
            username_like=username_like,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "",
    response_model=UserOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="创建账号（默认无角色，单独 add-role）",
)
async def create_user(
    payload: UserCreateRequest,
    svc: UserService = Depends(get_user_service),
) -> UserOut:
    return await svc.create_user(payload)


@router.get("/{user_id}", response_model=UserOut, summary="账号详情")
async def get_user(
    user_id: int,
    svc: UserService = Depends(get_user_service),
) -> UserOut:
    return await svc.get_user(user_id)


@router.post(
    "/{user_id}/update",
    response_model=UserOut,
    summary="更新账号（partial）",
)
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    svc: UserService = Depends(get_user_service),
) -> UserOut:
    return await svc.update_user(user_id, payload)


@router.post(
    "/{user_id}/deactivate",
    response_model=UserOut,
    summary="停用账号（软删）",
)
async def deactivate_user(
    user_id: int,
    svc: UserService = Depends(get_user_service),
) -> UserOut:
    return await svc.soft_delete_user(user_id)


# ============================================================
# 角色子资源
# ============================================================
@router.get(
    "/{user_id}/roles",
    response_model=list[UserRoleOut],
    summary="列出账号的所有角色",
)
async def list_user_roles(
    user_id: int,
    svc: UserService = Depends(get_user_service),
) -> list[UserRoleOut]:
    return await svc.list_user_roles(user_id)


@router.post(
    "/{user_id}/roles",
    response_model=UserRoleOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="给账号添加角色（SHELF_ACCOUNT 需 scope_type/scope_id）",
)
async def add_user_role(
    user_id: int,
    payload: UserAddRoleRequest,
    svc: UserService = Depends(get_user_service),
) -> UserRoleOut:
    return await svc.add_role(
        user_id=user_id,
        role=payload.role,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
    )


@router.post(
    "/{user_id}/roles/{role_id}/remove",
    summary="移除账号的一个角色",
)
async def remove_user_role(
    user_id: int,
    role_id: int,
    svc: UserService = Depends(get_user_service),
) -> dict:
    await svc.remove_role(user_id, role_id)
    return {"ok": True}
