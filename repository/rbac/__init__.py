from .permission import PermissionRepository
from .role import RoleRepository
from .role_permission import RolePermissionRepository
from .user_role import UserRoleRepository

__all__ = [
    "PermissionRepository",
    "RoleRepository",
    "RolePermissionRepository",
    "UserRoleRepository",
]
