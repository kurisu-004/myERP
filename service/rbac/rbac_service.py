"""RBAC 管理服务: 用户/角色/权限 CRUD + 关联分配."""
from __future__ import annotations

from fastapi import status

from core.error_code import ErrCode
from core.exception import BizError
from core.permission_cache import permission_cache
from model import TPermission, TRole, TUser
from repository.unit_of_work import UnitOfWork
from schema.rbac import (
    PermissionOut,
    RoleOut,
    RolePage,
    UserDetailOut,
    UserOut,
    UserPage,
)
from utils.id_gen import new_id
from utils.password import hash_password


class RBACService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    # ============================================================
    #  User
    # ============================================================
    async def list_users(
        self, page: int, size: int, keyword: str | None = None
    ) -> UserPage:
        items, total = await self.uow.users.list_paginated(
            page=page, size=size, keyword=keyword
        )
        return UserPage(
            items=[UserOut.model_validate(u) for u in items],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size if size else 0,
        )

    async def create_user(
        self,
        employee_no: str,
        username: str,
        name: str,
        password: str,
        role_ids: list[int],
    ) -> UserOut:
        async with self.uow.session.begin_nested():
            if await self.uow.users.get_by_username(username) is not None:
                raise BizError(
                    code=ErrCode.BIZ_USER_DUPLICATE,
                    message="username already exists",
                    http_status=status.HTTP_409_CONFLICT,
                )
            if await self.uow.users.get_by_employee_no(employee_no) is not None:
                raise BizError(
                    code=ErrCode.BIZ_USER_DUPLICATE,
                    message="employee_no already exists",
                    http_status=status.HTTP_409_CONFLICT,
                )
            user = TUser(
                id=new_id(),
                employee_no=employee_no,
                username=username,
                name=name,
                password_hash=hash_password(password),
            )
            await self.uow.users.create(user)
            for rid in role_ids:
                role = await self.uow.roles.get_by_id(rid)
                if role is None:
                    raise BizError(
                        code=ErrCode.BIZ_ROLE_NOT_FOUND,
                        message=f"role {rid} not found",
                        http_status=status.HTTP_404_NOT_FOUND,
                    )
                await self.uow.user_roles.assign(user.id, rid)
        await self.uow.commit()
        await permission_cache.delete(f"user_perms:{user.id}")
        return UserOut.model_validate(user)

    async def update_user(
        self,
        user_id: int,
        name: str | None = None,
        password: str | None = None,
        is_active: int | None = None,
        role_ids: list[int] | None = None,
    ) -> UserOut:
        async with self.uow.session.begin_nested():
            user = await self.uow.users.get_by_id(user_id)
            if user is None:
                raise BizError(
                    code=ErrCode.BIZ_USER_NOT_FOUND,
                    message=f"user {user_id} not found",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            if name is not None:
                user.name = name
            if password is not None:
                user.password_hash = hash_password(password)
            if is_active is not None:
                user.is_active = is_active
            await self.uow.users.update(user)
            if role_ids is not None:
                for rid in role_ids:
                    role = await self.uow.roles.get_by_id(rid)
                    if role is None:
                        raise BizError(
                            code=ErrCode.BIZ_ROLE_NOT_FOUND,
                            message=f"role {rid} not found",
                            http_status=status.HTTP_404_NOT_FOUND,
                        )
                await self.uow.user_roles.replace(user.id, role_ids)
        await self.uow.commit()
        await permission_cache.delete(f"user_perms:{user_id}")
        return UserOut.model_validate(user)

    async def delete_user(self, user_id: int) -> None:
        async with self.uow.session.begin_nested():
            user = await self.uow.users.get_by_id(user_id)
            if user is None:
                raise BizError(
                    code=ErrCode.BIZ_USER_NOT_FOUND,
                    message=f"user {user_id} not found",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            await self.uow.users.soft_delete(user)
        await self.uow.commit()
        await permission_cache.delete(f"user_perms:{user_id}")

    async def get_user_detail(self, user_id: int) -> UserDetailOut:
        user = await self.uow.users.get_by_id(user_id)
        if user is None:
            raise BizError(
                code=ErrCode.BIZ_USER_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        roles = await self.uow.user_roles.list_by_user(user_id)
        from schema.rbac import RoleBriefOut

        return UserDetailOut(
            id=user.id,
            employee_no=user.employee_no,
            username=user.username,
            name=user.name,
            is_active=user.is_active,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
            roles=[RoleBriefOut.model_validate(r) for r in roles],
        )

    # ============================================================
    #  Role
    # ============================================================
    async def list_roles(
        self, page: int, size: int, keyword: str | None = None
    ) -> RolePage:
        items, total = await self.uow.roles.list_paginated(
            page=page, size=size, keyword=keyword
        )
        return RolePage(
            items=[RoleOut.model_validate(r) for r in items],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size if size else 0,
        )

    async def list_all_roles(self) -> list[RoleOut]:
        rows = await self.uow.roles.list_all()
        return [RoleOut.model_validate(r) for r in rows]

    async def create_role(
        self,
        code: str,
        name: str,
        description: str | None,
        permission_ids: list[int],
    ) -> RoleOut:
        async with self.uow.session.begin_nested():
            if await self.uow.roles.get_by_code(code) is not None:
                raise BizError(
                    code=ErrCode.CONFLICT,
                    message=f"role code {code} already exists",
                    http_status=status.HTTP_409_CONFLICT,
                )
            for pid in permission_ids:
                if await self.uow.permissions.get_by_id(pid) is None:
                    raise BizError(
                        code=ErrCode.BIZ_PERMISSION_NOT_FOUND,
                        message=f"permission {pid} not found",
                        http_status=status.HTTP_404_NOT_FOUND,
                    )
            role = TRole(
                id=new_id(),
                code=code,
                name=name,
                description=description,
                is_builtin=0,
            )
            await self.uow.roles.create(role)
            for pid in permission_ids:
                await self.uow.role_permissions.grant(role.id, pid)
        await self.uow.commit()
        return RoleOut.model_validate(role)

    async def update_role(
        self,
        role_id: int,
        name: str | None = None,
        description: str | None = None,
        permission_ids: list[int] | None = None,
    ) -> RoleOut:
        async with self.uow.session.begin_nested():
            role = await self.uow.roles.get_by_id(role_id)
            if role is None:
                raise BizError(
                    code=ErrCode.BIZ_ROLE_NOT_FOUND,
                    message=f"role {role_id} not found",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            if name is not None:
                role.name = name
            if description is not None:
                role.description = description
            await self.uow.roles.update(role)
            if permission_ids is not None:
                for pid in permission_ids:
                    if await self.uow.permissions.get_by_id(pid) is None:
                        raise BizError(
                            code=ErrCode.BIZ_PERMISSION_NOT_FOUND,
                            message=f"permission {pid} not found",
                            http_status=status.HTTP_404_NOT_FOUND,
                        )
                await self.uow.role_permissions.replace(role_id, permission_ids)
        await self.uow.commit()
        return RoleOut.model_validate(role)

    async def delete_role(self, role_id: int) -> None:
        async with self.uow.session.begin_nested():
            role = await self.uow.roles.get_by_id(role_id)
            if role is None:
                raise BizError(
                    code=ErrCode.BIZ_ROLE_NOT_FOUND,
                    message=f"role {role_id} not found",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            if role.is_builtin:
                raise BizError(
                    code=ErrCode.BIZ_ROLE_BUILTIN,
                    message="builtin role cannot be deleted",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
            await self.uow.roles.soft_delete(role)
        await self.uow.commit()

    # ============================================================
    #  Permission
    # ============================================================
    async def list_permissions(self) -> list[PermissionOut]:
        rows = await self.uow.permissions.list_all(include_disabled=True)
        return [PermissionOut.model_validate(p) for p in rows]

    async def create_permission(
        self,
        code: str,
        name: str,
        type: str,
        parent_id: int | None,
        path: str | None,
        icon: str | None,
        component: str | None,
        sort_order: int,
        is_enabled: int,
    ) -> PermissionOut:
        async with self.uow.session.begin_nested():
            if await self.uow.permissions.get_by_code(code) is not None:
                raise BizError(
                    code=ErrCode.CONFLICT,
                    message=f"permission code {code} already exists",
                    http_status=status.HTTP_409_CONFLICT,
                )
            if parent_id is not None:
                if await self.uow.permissions.get_by_id(parent_id) is None:
                    raise BizError(
                        code=ErrCode.BIZ_PERMISSION_NOT_FOUND,
                        message=f"parent permission {parent_id} not found",
                        http_status=status.HTTP_404_NOT_FOUND,
                    )
            perm = TPermission(
                id=new_id(),
                code=code,
                name=name,
                type=type,
                parent_id=parent_id,
                path=path,
                icon=icon,
                component=component,
                sort_order=sort_order,
                is_enabled=is_enabled,
            )
            await self.uow.permissions.create(perm)
        await self.uow.commit()
        return PermissionOut.model_validate(perm)

    async def update_permission(
        self,
        permission_id: int,
        name: str | None = None,
        parent_id: int | None = None,
        path: str | None = None,
        icon: str | None = None,
        component: str | None = None,
        sort_order: int | None = None,
        is_enabled: int | None = None,
    ) -> PermissionOut:
        async with self.uow.session.begin_nested():
            perm = await self.uow.permissions.get_by_id(permission_id)
            if perm is None:
                raise BizError(
                    code=ErrCode.BIZ_PERMISSION_NOT_FOUND,
                    message=f"permission {permission_id} not found",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            if name is not None:
                perm.name = name
            if parent_id is not None:
                perm.parent_id = parent_id
            if path is not None:
                perm.path = path
            if icon is not None:
                perm.icon = icon
            if component is not None:
                perm.component = component
            if sort_order is not None:
                perm.sort_order = sort_order
            if is_enabled is not None:
                perm.is_enabled = is_enabled
            await self.uow.permissions.update(perm)
        await self.uow.commit()
        return PermissionOut.model_validate(perm)
