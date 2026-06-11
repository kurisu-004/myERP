from pydantic import BaseModel, ConfigDict, Field

from schema.order import OrderOut


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=50, examples=["alice"])


class UserPage(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    size: int
    pages: int


class UserWithOrdersOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    orders: list[OrderOut]
