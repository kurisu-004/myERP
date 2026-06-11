from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    price: Decimal


class OrderCreate(BaseModel):
    price: Decimal = Field(..., gt=0, examples=[99.90])
