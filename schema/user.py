"""账号 / 角色 / 登录 相关 Pydantic schema。

登录请求 / 响应字段都用 IdStr / IdStrNonNull（Pydantic 序列化时自动
把 BigInteger 写成字符串，防止 JS 精度截断）。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import UserRole
from schema._types import IdStr, IdStrNonNull

# 避免循环导入：菜单 schema 只在 CurrentUserOut 引用一次，由 Pydantic forward ref 解析。
from schema.menu import MenuNodeOut  # noqa: E402


# ============================================================
# 角色
# ============================================================
class UserRoleOut(BaseModel):
    id: IdStrNonNull
    role: str                 # UserRole.value
    scope_type: str | None = None
    scope_id: IdStr | None = None
    shelf_code: str | None = Field(default=None, description="SHELF_ACCOUNT 时拼入")
    shelf_name: str | None = Field(default=None, description="SHELF_ACCOUNT 时拼入")

    model_config = ConfigDict(from_attributes=True)


class UserAddRoleRequest(BaseModel):
    role: UserRole
    scope_type: str | None = Field(default=None, description="通常 'shelf'")
    scope_id: int | None = Field(default=None, description="scope_type='shelf' 时为 shelf.id")

    @field_validator("role", mode="before")
    @classmethod
    def _norm(cls, v):
        if isinstance(v, UserRole):
            return v
        return UserRole(v)


# ============================================================
# User CRUD
# ============================================================
class UserOut(BaseModel):
    id: IdStrNonNull
    username: str
    full_name: str
    phone: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    roles: list[UserRoleOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    is_active: bool | None = None


class UserListQuery(BaseModel):
    username_like: str | None = None
    is_active: bool | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int


# ============================================================
# 登录 / 会话
# ============================================================
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class CurrentUserOut(BaseModel):
    """GET /auth/me 与登录响应的 user 部分。"""
    id: IdStrNonNull
    username: str
    full_name: str
    is_active: bool
    roles: list[str] = Field(default_factory=list)
    shelf_ids: list[IdStr] = Field(default_factory=list)
    menus: list[MenuNodeOut] = Field(
        default_factory=list,
        description="该用户可见的菜单树（按 t_role_menu 解析，按 sort_order 排序）",
    )


class LoginResponse(BaseModel):
    token: str
    user: CurrentUserOut
