"""RBAC pydantic schema."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# -------- Login --------
class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# -------- User --------
class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_no: str
    username: str
    name: str
    is_active: int


class UserOut(UserBase):
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UserPage(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    size: int
    pages: int


class UserCreateIn(BaseModel):
    employee_no: str = Field(..., min_length=1, max_length=20)
    username: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    role_ids: list[int] = Field(default_factory=list)


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    is_active: int | None = Field(default=None, ge=0, le=1)
    role_ids: list[int] | None = None


class UserDetailOut(UserOut):
    roles: list["RoleBriefOut"] = Field(default_factory=list)


# -------- Role --------
class RoleBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None
    is_builtin: int


class RoleBriefOut(RoleBase):
    pass


class RoleOut(RoleBase):
    created_at: datetime
    updated_at: datetime


class RoleCreateIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    permission_ids: list[int] = Field(default_factory=list)


class RoleUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    permission_ids: list[int] | None = None


class RolePage(BaseModel):
    items: list[RoleOut]
    total: int
    page: int
    size: int
    pages: int


# -------- Permission --------
class PermissionBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    type: str
    parent_id: int | None = None
    path: str | None = None
    icon: str | None = None
    component: str | None = None
    sort_order: int
    is_enabled: int


class PermissionOut(PermissionBase):
    created_at: datetime
    updated_at: datetime


class PermissionCreateIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=50)
    type: str = Field(..., min_length=1, max_length=20)
    parent_id: int | None = None
    path: str | None = Field(default=None, max_length=255)
    icon: str | None = Field(default=None, max_length=50)
    component: str | None = Field(default=None, max_length=255)
    sort_order: int = 0
    is_enabled: int = 1


class PermissionUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    parent_id: int | None = None
    path: str | None = Field(default=None, max_length=255)
    icon: str | None = Field(default=None, max_length=50)
    component: str | None = Field(default=None, max_length=255)
    sort_order: int | None = None
    is_enabled: int | None = Field(default=None, ge=0, le=1)


# -------- Menu / Me --------
class MenuNodeOut(BaseModel):
    id: int
    code: str
    name: str
    path: str | None = None
    icon: str | None = None
    component: str | None = None
    sort_order: int = 0
    children: list["MenuNodeOut"] = Field(default_factory=list)


MenuNodeOut.model_rebuild()


class MeOut(BaseModel):
    user: UserOut
    roles: list[RoleBriefOut]
    permissions: list[str]
    menus: list[MenuNodeOut]
    is_superadmin: bool
